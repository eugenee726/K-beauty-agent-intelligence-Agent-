"""
backfill_terms.py
──────────────────
사람이 승인한 추가 용어(dim_term)의 과거 주차 신호를 저장된 원문(fact_sns_raw)에서 채운다.
`python main.py term approve` 가 승인 직후 자동으로 호출한다.

왜: 새 용어는 과거 이력이 없어 z(과거 3주 이상 필요)를 바로 계산할 수 없다.
    API 재수집 없이 이미 저장된 캡션에서 같은 규칙(단어 경계 일치)으로 세면 바로 탐지할 수 있다.

지키는 것:
  - 기존 (주차, 플랫폼, 키워드) 행은 절대 바꾸지 않는다. 없는 행만 추가한다.
  - 게시물 수는 원문 기준 정확값. engagement 는 원문에 댓글 수가 없어 조회수·좋아요로만 계산(수집기와 같은 가중치).
  - 2026-11-27(W49 수집) 이전 원문은 사전 용어가 하나라도 있어야 저장됐다 → 그 이전 주차에서 새 용어'만'
    언급한 게시물은 없어 과거 건수가 하한. 이후 원문은 전부 저장하므로 정확하다.
    coverage() 로 새 수집분에서 그런 게시물 비율을 재서 영향 크기를 확인한다.

사용:
  python main.py backfill-terms            # 승인된 모든 추가 용어의 빠진 과거 행 채우기
  python main.py backfill-terms --dry-run  # 쓰지 않고 주차별 건수만
"""

import json
import logging
import re
import sqlite3

from tools.collect.sns_terms import approved_terms, normalize

logger = logging.getLogger(__name__)

# 수집기와 같은 engagement 가중치 (댓글 항은 원문에 없어 제외)
ENGAGEMENT = {
    "youtube": lambda views, likes: views * 0.2 + likes * 1.5,   # youtube_tool: + comments * 3.0
    "tiktok":  lambda views, likes: views * 0.3 + likes * 1.0,   # tiktok_tool:  + comments * 2.0
}


def _pattern(keyword: str) -> re.Pattern:
    return re.compile(r"\b" + re.escape(keyword.replace("_", " ")) + r"\b")


def _text(content: str | None, hashtags: str | None) -> str:
    try:
        tags = json.loads(hashtags or "[]")
        tags = " ".join(tags) if isinstance(tags, list) else str(tags)
    except (TypeError, ValueError):
        tags = hashtags or ""
    return f"{content or ''} {tags}".lower()


def backfill(db_path: str, keywords: list[str] | None = None, dry_run: bool = False) -> dict[str, dict[str, int]]:
    """
    새 용어별·주차별 신호 행을 추가한다. 이미 있는 행은 건너뛴다.
    반환: {week: {keyword: post_count}} — 추가한(또는 dry_run 이면 추가할) 행
    """
    keywords = [normalize(k) for k in keywords] if keywords else list(approved_terms(db_path))
    pats = {k: _pattern(k) for k in keywords}
    out: dict[str, dict[str, int]] = {}
    with sqlite3.connect(db_path, timeout=30) as conn:
        # 아직 수집 중인 주차(다음 주간 실행이 집계할 주)는 건너뛴다 — 주간 집계가 새 사전으로 직접 센다.
        # (TikTok 주간 집계는 이미 있는 키워드 행을 건너뛰므로, 여기서 미리 넣으면 갱신되지 않는다)
        from datetime import date
        from tools.collect.youtube_tool import YouTubeTool
        open_week = YouTubeTool.run_week_of(date.today())
        weeks = [r[0] for r in conn.execute(
            "SELECT DISTINCT week FROM fact_sns_raw WHERE week < ? ORDER BY week", (open_week,))]
        for week in weeks:
            for platform in ENGAGEMENT:
                rows = conn.execute(
                    "SELECT content_text, hashtags, view_count, like_count FROM fact_sns_raw "
                    "WHERE week=? AND platform_id=?", (week, platform)).fetchall()
                agg: dict[str, list] = {}
                for content, tags, views, likes in rows:
                    text = _text(content, tags)
                    for kw, p in pats.items():
                        if p.search(text):
                            a = agg.setdefault(kw, [0, 0, 0, 0.0])
                            a[0] += 1
                            a[1] += views or 0
                            a[2] += likes or 0
                            a[3] += ENGAGEMENT[platform](views or 0, likes or 0)
                for kw, (n, views, likes, eng) in agg.items():
                    if conn.execute("SELECT 1 FROM fact_sns_signals WHERE week=? AND platform_id=? AND keyword=?",
                                    (week, platform, kw)).fetchone():
                        continue
                    out.setdefault(week, {})[kw] = out.get(week, {}).get(kw, 0) + n
                    if dry_run:
                        continue
                    is_new = 0 if conn.execute(
                        "SELECT 1 FROM fact_sns_signals WHERE platform_id=? AND keyword=? AND week<?",
                        (platform, kw, week)).fetchone() else 1
                    conn.execute("""INSERT INTO fact_sns_signals
                            (week, platform_id, keyword, post_count, total_views, total_likes, total_comments,
                             engagement_score, growth_rate, is_new_keyword)
                            VALUES (?, ?, ?, ?, ?, ?, 0, ?, NULL, ?)""",
                                 (week, platform, kw, n, views, likes, eng, is_new))
    logger.info(f"backfill-terms: {'(dry-run) ' if dry_run else ''}{sum(len(v) for v in out.values())}행")
    return out


def coverage(db_path: str, week: str, keywords: list[str] | None = None) -> dict[str, tuple[int, int]]:
    """
    그 주 원문에서 새 용어 게시물 중 '기존 사전 용어가 하나도 없는' 게시물 수.
    추가 전 규칙이었다면 저장되지 않았을 게시물 → 이 비율이 크면 과거 건수(하한)와의 비교가 부풀려진다.
    반환: {keyword: (새 용어 게시물, 그중 기존 용어 없음)}
    """
    keywords = [normalize(k) for k in keywords] if keywords else list(approved_terms(db_path))
    added = {k.replace("_", " ") for k in keywords}
    from tools.collect.sns_terms import kbeauty_terms
    old = [_pattern(t) for t in kbeauty_terms(db_path) if t not in added]
    pats = {k: _pattern(k) for k in keywords}
    res = {k: [0, 0] for k in keywords}
    with sqlite3.connect(db_path, timeout=30) as conn:
        for content, tags in conn.execute(
                "SELECT content_text, hashtags FROM fact_sns_raw WHERE week=? AND platform_id IN ('tiktok','youtube')",
                (week,)):
            text = _text(content, tags)
            hits = [k for k, p in pats.items() if p.search(text)]
            if not hits:
                continue
            only_new = not any(p.search(text) for p in old)
            for k in hits:
                res[k][0] += 1
                res[k][1] += int(only_new)
    return {k: tuple(v) for k, v in res.items()}
