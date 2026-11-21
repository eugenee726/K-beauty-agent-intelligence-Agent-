"""
sns_terms.py
─────────────
TikTok / YouTube 수집기가 공유하는 K-beauty 키워드 사전과 수집 대상 목록.

이전에는 tiktok_tool.py 와 youtube_tool.py 에 같은 사전이 복사돼 있었고,
브랜드 부분이 하드코딩돼 있었다. 브랜드 용어와 전용 해시태그·검색어는
이제 dim_brand(brand_registry)에서 만든다.
"""

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


def kbeauty_terms(db_path: str) -> set[str]:
    with sqlite3.connect(db_path, timeout=10) as conn:
        brands = load_brands(conn, tracked_only=True)
    return GENERIC_TERMS | EXTRA_BRAND_TERMS | brand_terms(brands)


def target_hashtags(db_path: str) -> list[str]:
    with sqlite3.connect(db_path, timeout=10) as conn:
        tags, _ = dedicated_sns_targets(conn)
    return GENERIC_HASHTAGS + [t for t in tags if t not in GENERIC_HASHTAGS]


def target_queries(db_path: str) -> list[str]:
    with sqlite3.connect(db_path, timeout=10) as conn:
        _, queries = dedicated_sns_targets(conn)
    return GENERIC_QUERIES + [q for q in queries if q not in GENERIC_QUERIES]
