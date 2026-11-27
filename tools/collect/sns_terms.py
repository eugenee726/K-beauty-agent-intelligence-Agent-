"""
sns_terms.py
─────────────
TikTok / YouTube 수집기가 공유하는 K-beauty 키워드 사전과 수집 대상 목록.

사전 = 코드의 시작 사전(GENERIC_TERMS, 고정) + 사람이 승인한 추가 용어(dim_term, status='active')
       + 추적 브랜드 용어(dim_brand).
추가 용어는 매주 사전 밖 용어 후보(tools/analyze/term_candidates.py)를 보고 사람이 승인한다
(python main.py term approve ...). 승인하면 저장된 원문에서 과거 주차 신호를 채운다(backfill_terms.py).
"""

import re
import sqlite3

from tools.collect.brand_registry import brand_terms, dedicated_sns_targets, load_brands

# 성분·제품 유형·피부 고민·루틴·일반 용어 (브랜드 제외)
GENERIC_TERMS: set[str] = {
    # 성분
    "niacinamide", "hyaluronic acid", "ceramide", "retinol",
    "vitamin c", "centella", "cica", "snail mucin", "propolis",
    "tranexamic acid", "azelaic acid", "bakuchiol", "peptide",
    "salicylic acid", "glycolic acid", "aha", "bha", "spf",
    "panthenol", "allantoin", "beta glucan", "galactomyces",
    "mugwort", "ginseng", "green tea", "rice water", "fermented",
    "polyglutamic acid", "adenosine", "arbutin", "kojic acid",
    # 제품 유형
    "serum", "essence", "toner", "moisturizer", "cleanser",
    "sheet mask", "sleeping mask", "ampoule", "cushion",
    "sunscreen", "sunstick", "toner pad", "lip tint",
    "eye cream", "cleansing oil", "foam cleanser",
    # 피부 고민
    "acne", "pore", "wrinkle", "brightening", "dark spot",
    "redness", "sensitive skin", "barrier", "hyperpigmentation",
    # 루틴
    "skincare routine", "glass skin", "dewy skin",
    "skin cycling", "slugging", "double cleanse",
    "morning routine", "night routine", "layering",
    # K-beauty 일반
    "kbeauty", "k-beauty", "korean skincare", "korean beauty",
    "asian beauty",
}

# 추적 대상은 아니지만 이전부터 탐지하던 K-beauty 브랜드 (탐지는 유지)
EXTRA_BRAND_TERMS: set[str] = {"some by mi", "klairs", "peripera"}

# 브랜드와 무관한 일반 해시태그·검색어
GENERIC_HASHTAGS: list[str] = [
    "kbeauty", "koreanskincare", "kbeautyskincare",
    "oliveyoung", "koreanbeauty", "skincareroutine",
]
GENERIC_QUERIES: list[str] = [
    "kbeauty", "k-beauty skincare", "korean skincare routine",
    "korean beauty", "kbeauty review", "kbeauty haul", "oliveyoung",
    "skincare routine", "skincare product", "skincare review", "skincare haul",
    # 성분·니즈 일반어 (탐지 표본 틀 고정 — 트렌드에 따라 바꾸지 않는다)
    "korean sunscreen", "niacinamide serum", "snail mucin", "centella skincare", "retinol korean skincare",
    "toner pad", "korean lip tint", "cushion foundation", "pore care skincare", "acne korean skincare",
    "skin barrier repair", "pdrn skincare", "rice toner", "eye cream korean", "glass skin routine",
]


# 승인 사전: 시작 사전 이후 추가는 이 테이블에서만 한다 (코드 수정·배포 없이 사전이 자람)
TERM_SCHEMA = """CREATE TABLE IF NOT EXISTS dim_term (
    term        TEXT PRIMARY KEY,   -- 원형, 소문자·공백 구분 (예: 'pimple patch')
    status      TEXT NOT NULL,      -- 'active'(사전에 포함) | 'rejected'(후보에서 다시 보이지 않음)
    category    TEXT,               -- 성분 | 제품유형 | 고민 | 루틴 | 기타
    added_week  TEXT,               -- 이 용어로 정식 수집·집계가 시작되는 주차
    source      TEXT,               -- 'candidate:<주차>' | 'manual'
    note        TEXT,               -- 승인 근거 (예: 캡션 언급 수)
    decided_at  TEXT DEFAULT (datetime('now'))
)"""


def approved_terms(db_path: str) -> dict[str, str]:
    """승인된 추가 용어 {정규화 키워드: 정식 수집 시작 주차}."""
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute(TERM_SCHEMA)
        rows = conn.execute("SELECT term, added_week FROM dim_term WHERE status='active'").fetchall()
    return {normalize(t): w for t, w in rows}


def generic_terms(db_path: str) -> set[str]:
    """브랜드를 뺀 사전 = 시작 사전 + 승인된 추가 용어."""
    with sqlite3.connect(db_path, timeout=10) as conn:
        conn.execute(TERM_SCHEMA)
        extra = {r[0] for r in conn.execute("SELECT term FROM dim_term WHERE status='active'")}
    return GENERIC_TERMS | extra


def kbeauty_terms(db_path: str) -> set[str]:
    with sqlite3.connect(db_path, timeout=10) as conn:
        brands = load_brands(conn, tracked_only=True)
    return generic_terms(db_path) | EXTRA_BRAND_TERMS | brand_terms(brands)


def normalize(term: str) -> str:
    """수집기와 같은 키워드 정규화 ('pimple patch' → 'pimple_patch')."""
    return re.sub(r"[\s\-&]+", "_", term.strip())


def terms_regex(terms) -> re.Pattern:
    """사전 전체를 한 번에 찾는 정규식 (수집기 _extract_keywords 와 같은 단어 경계 규칙)."""
    alts = sorted((re.escape(t) for t in terms), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(alts) + r")\b")


def dictionary_post_counts(db_path: str) -> dict[str, dict[str, int]]:
    """
    주차·플랫폼별로 사전 용어가 하나라도 있는 원문 게시물 수 {week: {platform: n}}.
    탐지 분모(게시물 비중)와 화면의 '분석한 게시물'에 쓴다. 원문은 사전과 무관하게 전부 저장하므로
    COUNT(*) 대신 이 값을 써야 사전 밖 게시물이 늘어도 비중이 흔들리지 않는다.
    (원문 전체 저장 이전 주차는 사전 용어가 있는 게시물만 저장돼 있어 두 값이 같다)
    """
    import json
    pat = terms_regex(kbeauty_terms(db_path))
    out: dict[str, dict[str, int]] = {}
    with sqlite3.connect(db_path, timeout=30) as conn:
        for week, platform, text, tags in conn.execute(
                "SELECT week, platform_id, content_text, hashtags FROM fact_sns_raw "
                "WHERE platform_id IN ('tiktok','youtube')"):
            try:
                t = json.loads(tags or "[]")
                t = " ".join(t) if isinstance(t, list) else str(t)
            except (TypeError, ValueError):
                t = tags or ""
            if pat.search(f"{text or ''} {t}".lower()):
                d = out.setdefault(week, {})
                d[platform] = d.get(platform, 0) + 1
    return out


def target_hashtags(db_path: str) -> list[str]:
    with sqlite3.connect(db_path, timeout=10) as conn:
        tags, _ = dedicated_sns_targets(conn)
    return GENERIC_HASHTAGS + [t for t in tags if t not in GENERIC_HASHTAGS]


def target_queries(db_path: str) -> list[str]:
    with sqlite3.connect(db_path, timeout=10) as conn:
        _, queries = dedicated_sns_targets(conn)
    return GENERIC_QUERIES + [q for q in queries if q not in GENERIC_QUERIES]
