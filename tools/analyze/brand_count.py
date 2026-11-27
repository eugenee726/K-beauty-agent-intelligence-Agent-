"""
brand_count.py
───────────────
트렌드 키워드 캡션에서 브랜드 언급을 코드로 직접 센다 (결정적 집계).

- 대상: 그 주 키워드가 들어간 캡션 전체 = 탐지용 수집(fact_sns_raw) + 트렌드 심층 수집(fact_sns_deep)
- 방법: dim_brand 의 영문명·brand_id·aliases(3자 이상)를 단어 경계 정규식으로 찾고, 캡션당 브랜드 1회만 센다
- 쓰는 곳: 대화 점유(기회 유형) 판정(agents/decision_agent.py), 대시보드 점유 표시(tools/report/dashboard_builder.py)

LLM 이 센 브랜드 수는 쓰지 않는다. LLM 은 입력에 같이 들어간 제품 카탈로그의 브랜드를
캡션에 없는데도 언급으로 세는 경우가 있었기 때문이다(2026-W47 검증에서 확인).
"""

import json
import re
import sqlite3

# 일반 영어 단어와 같아 오탐이 나는 브랜드명
BRAND_STOPWORDS = {"glow", "luna", "naming", "risky", "dove", "ideal", "about", "the phi", "fation", "finv"}


def brand_patterns(conn: sqlite3.Connection) -> list[tuple[re.Pattern, str]]:
    """dim_brand 의 영문명·brand_id·aliases(3자 이상) → (단어 경계 정규식, brand_id)."""
    names: dict[str, str] = {}
    for bid, name_en, aliases in conn.execute("SELECT brand_id, brand_name_en, aliases FROM dim_brand"):
        try:
            al = json.loads(aliases) if aliases else []
        except (TypeError, ValueError):
            al = []
        for n in [name_en or "", bid.replace("_", " ")] + (al if isinstance(al, list) else []):
            n = str(n).lower().strip()
            if len(n) >= 3 and n not in BRAND_STOPWORDS:
                names[n] = bid
    return [(re.compile(r"(?<![a-z0-9])" + re.escape(n) + r"(?![a-z0-9])"), b) for n, b in names.items()]


def count_brands(conn: sqlite3.Connection, week: str, keyword: str,
                 patterns: list[tuple[re.Pattern, str]] | None = None) -> dict:
    """
    키워드 캡션 전체에서 브랜드별 언급 캡션 수.
    반환: {"captions": 캡션 수, "deep": 심층 수집분 행 수, "total": 언급 합계, "brands": [(brand_id, n), ...] 내림차순}
    """
    patterns = patterns if patterns is not None else brand_patterns(conn)
    plain = keyword.replace("_", " ").lower()
    low = keyword.lower()
    like = (f"%{low}%", f"%{plain}%", f"%{low}%")
    sql = """SELECT post_id, content_text, hashtags FROM {t} WHERE week=?
             AND (LOWER(content_text) LIKE ? OR LOWER(content_text) LIKE ? OR LOWER(hashtags) LIKE ?)"""
    rows = list(conn.execute(sql.format(t="fact_sns_raw"), (week, *like)))
    has_deep = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='fact_sns_deep'").fetchone()
    deep = list(conn.execute(sql.format(t="fact_sns_deep") + " AND keyword=?", (week, *like, keyword))) \
        if has_deep else []
    seen, counts, n_cap = set(), {}, 0
    for post_id, text, tags in rows + deep:
        if post_id in seen:
            continue
        seen.add(post_id)
        n_cap += 1
        blob = f"{text or ''} {tags or ''}".lower()
        for b in {bid for p, bid in patterns if p.search(blob)}:
            counts[b] = counts.get(b, 0) + 1
    # 동점은 brand_id 순 — 위 set 순회 순서가 실행마다 달라 동점 순서(와 화면의 1위)가 바뀌던 문제 방지
    top = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return {"captions": n_cap, "deep": len(deep), "total": sum(counts.values()), "brands": top}
