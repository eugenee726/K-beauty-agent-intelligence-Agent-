"""
ai_review.py
─────────────
트렌드·제품마다 'AI 해석'(참고용)을 만든다: 뷰티 마케터 역할의 LLM이 근거를 읽고
신호의 의미와 신뢰도, 검토할 것, 다음 주 확인할 것을 한 줄씩 쓴다.

원칙
  - 판정은 바꾸지 않는다. 모양·대화 점유·4분면은 코드 판정 그대로이고, 규칙 기반 제안(action_rec)도 그대로 둔다.
  - 실행 지시(콘텐츠 형식·채널별 게시물 등)는 쓰지 않는다. 근거가 그 수준을 뒷받침하지 못하기 때문.
    정답이 아니라 기획 회의 전에 참고할 해석과 검토 포인트다.
  - 코드 규칙 검사(check)에 걸리면 저장하지 않는다 → 화면에는 규칙 기반 제안만 보인다.

저장
  fact_trend_insights.ai_review   : "의미: …\\n검토할 것: …\\n확인: …"   (상황은 action_rec 의 상황 줄을 그대로 쓴다)
  fact_product_insights.ai_review : "상황: …\\n의미: …\\n검토할 것: …\\n확인: …"
  리뷰 원문이 없는 제품은 근거가 부족해 만들지 않는다.

모델: ANTHROPIC_MODEL (인사이트 생성과 같은 모델). 출력은 JSON 스키마로 고정(output_config.format).
"""

import json
import logging
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import anthropic

from tools.analyze.brand_count import brand_patterns, count_brands
from tools.analyze.llm_insight_tool import sample_captions

logger = logging.getLogger(__name__)

WORKERS = 4          # 동시 요청 수
N_CAPTIONS = 8       # 트렌드 근거 캡션 수 (인사이트 생성과 같은 샘플링, 조회수 상위)
N_REVIEWS = 8        # 제품 근거 리뷰 수 (최신·도움됨 순)
MAX_LINE = 130       # 한 줄 최대 글자 (목표는 100자 안팎)

ROLE = """당신은 미국 시장을 담당하는 10년 차 K-뷰티 브랜드 마케터다. 특정 회사 소속이 아니다.
주간 기획 회의 전에 동료 마케터에게 이 신호를 어떻게 읽을지, 무엇을 검토하면 좋을지 짚어 준다.
실행안(콘텐츠 형식, 채널별 게시물, 카드뉴스, 촬영 방법 등)을 지시하지 않는다. 그것은 각 브랜드 팀이 자기 상황에 맞춰 정한다.
정답이 아니라 판단을 돕는 관점이다.

지킬 것:
- [근거]에 있는 사실만 쓴다. 근거에 없는 효능·임상·가격·수치는 쓰지 않는다.
- 신호의 신뢰도를 함께 판단한다: 표본(게시물·리뷰) 수, 한 게시물에 쏠림, 수집 편중을 보고 약한 신호면 약하다고 말한다.
- 특정 브랜드를 추천하거나 깎아내리지 않는다. "우리", "자사"는 쓰지 않는다.
- 각 줄은 한국어 한 문장, 100자 안팎(최대 110자), 줄바꿈 없이 쓴다."""

_LINE = {"type": "string"}
TREND_SCHEMA = {"type": "object", "properties": {
    "meaning": {**_LINE, "description": "의미: 소비자가 원하는 것 + 이 신호를 얼마나 믿을 수 있는지"},
    "review": {**_LINE, "description": "검토할 것: 대응할지 관찰할지, 대응한다면 어느 팀이 어떤 각도로 볼지 (실행 지시 금지)"},
    "watch": {**_LINE, "description": "확인: 다음 주에 볼 신호 하나"}},
    "required": ["meaning", "review", "watch"], "additionalProperties": False}
PRODUCT_SCHEMA = {"type": "object", "properties": {
    "situation": {**_LINE, "description": "상황: 지표와 리뷰로 본 현재 상태"},
    "meaning": {**_LINE, "description": "의미: 리뷰가 말하는 핵심(좋은 점·걸림돌) + 표본 신뢰도"},
    "review": {**_LINE, "description": "검토할 것: 어느 담당이 무엇을 검토할지 (실행 지시 금지)"},
    "watch": {**_LINE, "description": "확인: 다음 주에 볼 신호 하나"}},
    "required": ["situation", "meaning", "review", "watch"], "additionalProperties": False}
TREND_LABELS = (("meaning", "의미"), ("review", "검토할 것"), ("watch", "확인"))
PRODUCT_LABELS = (("situation", "상황"), ("meaning", "의미"), ("review", "검토할 것"), ("watch", "확인"))
QUAD = {"PUSH_NOW": "긍정 리뷰 + 별점 상승 → 확대", "FIX_AND_PUSH": "불만 신호(별점 하락 또는 부정 35% 이상) → 개선 후 확대",
        "HOLD": "긍정이지만 별점 상승 없음 → 유지", "MONITOR": "그 외 → 관찰"}
SHAPE = {"spike": "이번 주 급등", "sustained": "3주 연속 상승", "emerging": "신규 등장", "steady": "꾸준한 수요(트렌드 기준 미달)"}
OPP = {"leader": "우세", "contested": "각축", "white_space": "공백"}

_FORBIDDEN = ("우리", "자사", "당사")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"|“[^”]*”|‘[^’]*’")


def ensure_columns(conn) -> None:
    for table in ("fact_trend_insights", "fact_product_insights"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "ai_review" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN ai_review TEXT")


def _label(conn, bid: str) -> str:
    r = conn.execute("SELECT brand_name_en FROM dim_brand WHERE brand_id=?", (bid,)).fetchone()
    return ((r[0] if r and r[0] else bid) or "").upper()


def _clean(t: str, n: int) -> str:
    return re.sub(r"\s+", " ", t or "").strip()[:n]


# ── 근거 묶음 (모두 DB 에 이미 있는 값) ──
def trend_prompt(conn, week: str, kw: str, row: dict, patterns) -> str:
    det = count_brands(conn, week, kw, patterns)
    total = det["total"]
    share = ", ".join(f"{_label(conn, b)} {round(100 * n / total)}%({n}건)" for b, n in det["brands"][:3]) if total else "K-브랜드 언급 없음"
    ins = conn.execute("SELECT consumer_language FROM fact_llm_insights WHERE week=? AND keyword=? ORDER BY id DESC LIMIT 1",
                       (week, kw)).fetchone()
    prods = conn.execute("""
        SELECT b.brand_name_en, p.product_name_en, lip.match_type FROM fact_llm_insight_products lip
        JOIN dim_product p ON p.product_id = lip.product_id JOIN dim_brand b ON b.brand_id = p.brand_id
        WHERE lip.week=? AND lip.keyword=? ORDER BY CASE lip.match_type WHEN 'direct' THEN 0 ELSE 1 END, lip.id LIMIT 5""",
                         (week, kw)).fetchall()
    caps = sample_captions(conn, kw, week, N_CAPTIONS)
    posts = conn.execute("SELECT SUM(post_count) FROM fact_sns_signals WHERE week=? AND keyword=? AND platform_id IN ('tiktok','youtube')",
                         (week, kw)).fetchone()[0] or 0
    plain = kw.replace("_", " ")
    views = [v[0] or 0 for v in conn.execute(
        "SELECT view_count FROM fact_sns_raw WHERE week=? AND (LOWER(content_text) LIKE ? OR LOWER(content_text) LIKE ? OR LOWER(hashtags) LIKE ?)",
        (week, f"%{kw}%", f"%{plain}%", f"%{kw}%"))]
    top = round(100 * max(views) / sum(views)) if sum(views) else 0
    plat = dict(conn.execute("SELECT platform_id, COUNT(*) FROM fact_sns_raw WHERE week=? AND platform_id IN ('tiktok','youtube') GROUP BY 1",
                             (week,)).fetchall())
    yt = round(100 * plat.get("youtube", 0) / max(1, sum(plat.values())))
    lead = row.get("lead_platform")
    channel = ("TikTok·YouTube 동시" if row.get("is_cross_platform") else ("TikTok 중심" if lead == "tiktok" else "YouTube 중심")) if lead else "—"
    return f"""[키워드] {plain}
[모양] {SHAPE.get(row.get('trend_shape'), row.get('trend_shape'))} (z {row.get('z_score')}, 모멘텀 {row.get('momentum_score')}배)
[대화 점유] {OPP.get(row.get('opportunity_type'), row.get('opportunity_type'))} — 캡션 속 K-브랜드 언급 {total}건 중 {share}
[소비자 니즈] {row.get('consumer_need') or ''}
[소비자 표현] {(ins[0] if ins and ins[0] else '')}
[주 채널(참고)] {channel}
[연결 제품]
{chr(10).join(f"- {(b or '').upper()} {p} ({'캡션 직접 언급' if m == 'direct' else '니즈 연결'})" for b, p, m in prods) or '- 없음'}
[근거 캡션]
{chr(10).join(f"#{i} {_clean(c, 300)}" for i, c in enumerate(caps, 1))}
[신호 신뢰도] 이번 주 이 키워드 게시물 {posts}건 · 조회수 1위 게시물 비중 {top}% · 이번 주 수집 게시물 중 YouTube 비중 {yt}%(수집 방식 영향, 채널 판단 근거로 약함)

의미, 검토할 것, 확인을 각각 한 줄로 써라."""


def product_prompt(conn, week: str, row: dict) -> str | None:
    src = row.get("voc_source_week") or week
    revs = conn.execute("""SELECT rating, review_text FROM fact_raw_reviews WHERE product_id=? AND week<=?
                           ORDER BY week DESC, helpful DESC LIMIT ?""", (row["product_id"], src, N_REVIEWS)).fetchall()
    if not revs:
        return None
    trends = [t[0] for t in conn.execute("SELECT DISTINCT keyword FROM fact_llm_insight_products WHERE week=? AND product_id=?",
                                          (week, row["product_id"]))]
    j = lambda v: ", ".join(json.loads(v or "[]"))
    return f"""[제품] {row['brand_name_en']} {row['product_name_en']}
[현재 분면] {row['strategy_quad']} — {QUAD.get(row['strategy_quad'], '')}
[지표] 리뷰 긍정 {row['sentiment_pos']} · 부정 {row['sentiment_neg']} · 평균 별점 {row['avg_rating']} · 별점 주간 변화 {row.get('rating_trend')} · 리뷰 유입 {row.get('voc_velocity')} · OY 주문 순위 {row['oy_rank_orders']} · OY 한국 순위 {row['oy_rank_korea']}
[긍정 키워드] {j(row['pos_keywords'])}
[부정 키워드] {j(row['neg_keywords'])}
[개선 니즈] {j(row['needs_keywords'])}
[이 제품이 연결된 이번 주 SNS 키워드] {', '.join(trends) or '없음'}
[신호 신뢰도] 분석에 쓴 리뷰 표본 {len(revs)}건 (최신·도움됨 순), VOC 기준 주차 {src}
[리뷰]
{chr(10).join(f"#{i} ({r[0] or '-'}점) {_clean(r[1], 260)}" for i, r in enumerate(revs, 1))}

상황, 의미, 검토할 것, 확인을 각각 한 줄로 써라."""


def _known(n: str, known: set[str]) -> bool:
    """근거에 있는 숫자이거나, 근거 숫자를 반올림한 값(294.26 → 294, 0.616 → 0.62)이면 허용."""
    if n in known:
        return True
    try:
        v = float(n.replace(",", ""))
    except ValueError:
        return False
    for k in known:
        try:
            kv = float(k.replace(",", ""))
        except ValueError:
            continue
        digits = len(n.split(".")[1]) if "." in n else 0
        if round(kv, digits) == v:
            return True
    return False


# ── 안전장치: 지어낸 수치·근거 밖 브랜드·금지어·형식 ──
def check(out: dict, prompt: str, patterns) -> list[str]:
    from tools.quality.insight_checks import _hangul_ratio
    issues = []
    known_nums = set(_NUM.findall(prompt))
    prompt_brands = {bid for p, bid in patterns if p.search(prompt.lower())}
    for key, text in out.items():
        text = (text or "").strip()
        if not text:
            issues.append(f"{key}: 비어 있음"); continue
        if "\n" in text:
            issues.append(f"{key}: 여러 줄")
        for w in _FORBIDDEN:
            if w in text:
                issues.append(f"{key}: 금지어 '{w}'")
        if _hangul_ratio(_QUOTED.sub(" ", text)) < 0.4:
            issues.append(f"{key}: 한국어 비율 낮음")
        extra = [n for n in _NUM.findall(text) if not _known(n, known_nums)]
        if extra:
            issues.append(f"{key}: 근거에 없는 숫자 {extra}")
        brands = {bid for p, bid in patterns if p.search(text.lower())} - prompt_brands
        if brands:
            issues.append(f"{key}: 근거 밖 브랜드 {sorted(brands)}")
        if len(text) > MAX_LINE:
            issues.append(f"{key}: 너무 김 ({len(text)}자)")
    return issues


class AIReviewWriter:
    def __init__(self, db_path: str, model: str | None = None):
        self.db_path = db_path
        self.model = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
        self.client = anthropic.Anthropic()
        self.usage = {"input": 0, "output": 0}

    def _ask(self, schema: dict, prompt: str) -> dict:
        resp = self.client.messages.create(
            model=self.model, max_tokens=8000, system=ROLE,
            output_config={"format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": prompt}])
        self.usage["input"] += resp.usage.input_tokens
        self.usage["output"] += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            raise ValueError("refusal")
        text = "".join(b.text for b in resp.content if b.type == "text")
        if not text:
            raise ValueError(f"응답 없음 (stop_reason={resp.stop_reason})")
        return json.loads(text)

    def _one(self, kind: str, prompt: str, patterns):
        schema, labels = (TREND_SCHEMA, TREND_LABELS) if kind == "trend" else (PRODUCT_SCHEMA, PRODUCT_LABELS)
        try:
            out = self._ask(schema, prompt)
        except Exception as e:
            return None, [f"생성 실패: {type(e).__name__}: {e}"]
        issues = check(out, prompt, patterns)
        if issues:
            return None, issues
        return "\n".join(f"{label}: {out[k].strip()}" for k, label in labels), []

    def run(self, week: str, force: bool = False, products: bool = True, trends: bool = True) -> dict:
        """그 주 트렌드·제품의 AI 해석을 만들어 저장. force=False 면 이미 있는 행은 건너뛴다."""
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            ensure_columns(conn)
            patterns = brand_patterns(conn)
            jobs = []
            if trends:
                for r in conn.execute("SELECT * FROM fact_trend_insights WHERE week=?" + ("" if force else " AND ai_review IS NULL"),
                                      (week,)).fetchall():
                    jobs.append(("trend", "fact_trend_insights", r["id"], trend_prompt(conn, week, r["keyword"], dict(r), patterns)))
            if products:
                for r in conn.execute("""SELECT pi.*, p.product_name_en, b.brand_name_en FROM fact_product_insights pi
                        JOIN dim_product p ON p.product_id = pi.product_id JOIN dim_brand b ON b.brand_id = pi.brand_id
                        WHERE pi.week=?""" + ("" if force else " AND pi.ai_review IS NULL"), (week,)).fetchall():
                    prompt = product_prompt(conn, week, dict(r))
                    if prompt:
                        jobs.append(("product", "fact_product_insights", r["id"], prompt))
        stats = {"week": week, "jobs": len(jobs), "saved": 0, "rejected": 0, "issues": []}
        if not jobs:
            return stats
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = list(pool.map(lambda j: self._one(j[0], j[3], patterns), jobs))
        with sqlite3.connect(self.db_path, timeout=30) as conn:
            for (kind, table, rid, _), (text, issues) in zip(jobs, results):
                if text:
                    conn.execute(f"UPDATE {table} SET ai_review=? WHERE id=?", (text, rid))
                    stats["saved"] += 1
                else:
                    stats["rejected"] += 1
                    stats["issues"].append(f"{kind}#{rid}: {'; '.join(issues)}")
        stats["usage"] = dict(self.usage)
        logger.info(f"AI 해석 {week}: 대상 {stats['jobs']} · 저장 {stats['saved']} · 제외 {stats['rejected']} · 토큰 {self.usage}")
        return stats
