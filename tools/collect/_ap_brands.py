"""
_ap_brands.py
──────────────
아모레퍼시픽 계열 브랜드 감지 공통 모듈.
oy_global_tool.py / sephora_tool.py 에서 공유.
"""

# ── 아모레퍼시픽 계열 브랜드 (OY Global / Sephora 표기 기준) ──
AP_BRAND_KEYWORDS = {
    "sulwhasoo", "hera", "primera", "tata harper", "tataharper",
    "iope", "aestura", "mamonde", "hanyul",
    "laneige", "innisfree", "cosrx", "espoir", "etude",
}

# 브랜드명 표기 → dim_brand.brand_id 정규 매핑
_AP_BRAND_ID_MAP: dict[str, str] = {
    "sulwhasoo":   "sulwhasoo",
    "hera":        "hera",
    "primera":     "primera",
    "tata harper": "tata_harper",
    "tataharper":  "tata_harper",
    "iope":        "iope",
    "aestura":     "aestura",
    "mamonde":     "mamonde",
    "hanyul":      "hanyul",
    "laneige":     "laneige",
    "innisfree":   "innisfree",
    "cosrx":       "cosrx",
    "espoir":      "espoir",
    "etude":       "etude",
    "etude house": "etude",
}

# ── OY Global 브랜드 페이지 (실제 확인된 brandNo) ──────────────
# URL: /display/page/brand-page?brandNo=XXXXX
# SULWHASOO, Tata Harper는 OY Global 미입점 → Sephora 전용
OY_AP_BRAND_PAGES: list[tuple[str, str, str]] = [
    # (brand_id, 표시명, brandNo)
    ("cosrx",     "COSRX",     "B00095"),
    ("laneige",   "LANEIGE",   "B00280"),
    ("innisfree", "INNISFREE", "B00519"),
    ("iope",      "IOPE",      "B01210"),
    ("aestura",   "AESTURA",   "B00214"),
    ("mamonde",   "Mamonde",   "B00234"),
    ("hanyul",    "HANYUL",    "B00383"),
    ("hera",      "HERA",      "B01392"),
    ("etude",     "ETUDE",     "B00288"),
    ("espoir",    "espoir",    "B00210"),
    ("primera",   "Primera",   "B01191"),
]

# ── Sephora 브랜드 페이지 경로 ──────────────────────────────────
# SULWHASOO, Tata Harper는 OY Global 미입점이므로 Sephora에서만 수집
SEPHORA_AP_BRAND_PATHS: list[tuple[str, str]] = [
    ("cosrx",       "/brand/cosrx"),
    ("laneige",     "/brand/laneige"),
    ("innisfree",   "/brand/innisfree"),
    ("sulwhasoo",   "/brand/sulwhasoo"),
    ("tata_harper", "/brand/tata-harper"),
]


def is_ap_brand(brand: str) -> bool:
    """
    브랜드명이 아모레퍼시픽 계열인지 확인.
    단어 단위 매칭으로 false positive 방지.
    예) 'V-Thera' → hera 포함이지만 단어 단위로 'hera' 아님 → False
    """
    b = brand.lower().strip()
    # 정확히 일치하거나 단어 경계로 일치하는 경우만 AP
    for kw in AP_BRAND_KEYWORDS:
        if b == kw:
            return True
        # 단어 경계 패턴: 앞뒤가 공백, 특수문자, 문자열 시작/끝
        import re
        if re.search(rf'(?<![a-z]){re.escape(kw)}(?![a-z])', b):
            return True
    return False


def ap_brand_id(brand: str) -> str | None:
    """브랜드명 → dim_brand.brand_id. 아모레퍼시픽 아니면 None."""
    if not is_ap_brand(brand):
        return None
    b = brand.lower().strip()
    for key, bid in _AP_BRAND_ID_MAP.items():
        import re
        if b == key or re.search(rf'(?<![a-z]){re.escape(key)}(?![a-z])', b):
            return bid
    return None
