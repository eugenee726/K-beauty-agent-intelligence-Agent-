"""
_product_utils.py
──────────────────
dim_product 서로게이트 키 관리 + OY Global 내부 유사도 매핑 공통 유틸.

사용처: oy_global_tool.py
"""

import re
import sqlite3
import logging

from rapidfuzz import fuzz

logger = logging.getLogger(__name__)

# token_set_ratio 오판 방지를 위해 높게 유지
MATCH_THRESHOLD = 85


def next_product_id(conn: sqlite3.Connection) -> str:
    """
    dim_product에서 현재 MAX product_id를 읽어 다음 서로게이트 키 반환.
    형식: 'S00000001', 'S00000002', ...
    """
    row = conn.execute(
        "SELECT MAX(CAST(SUBSTR(product_id,2) AS INTEGER)) FROM dim_product "
        "WHERE product_id LIKE 'S%'"
    ).fetchone()
    last_num = row[0] or 0
    return f"S{last_num + 1:08d}"


def normalize_name(name: str) -> str:
    """제품명 정규화 — 유사도 비교용."""
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


# Sephora 마케팅 설명 시작 패턴
_SEP_DESC_PATTERN = re.compile(
    r"\s+(with|for|featuring|infused with|enriched with"
    r"|\-\s*(?:gently|deeply|softly|effectively|visibly|instantly|powerfully))\s+.+$",
    re.IGNORECASE,
)

# 카테고리·마케팅 수식어 (단독 토큰으로만 제거)
_FILLER_WORDS = re.compile(
    r"\b(moisturizer|moisturizing|treatment|formula|complex|solution"
    r"|facial|lightweight|milky|nourishing|hydrating|soothing"
    r"|brightening|firming|repairing|rebalancing|revitalizing)\b",
    re.IGNORECASE,
)

# 용량 패턴 (매칭 시만 제거 — 스토리지에는 유지)
_CAPACITY_PATTERN = re.compile(
    r"\b\d+\s*(ml|mL|g|oz|fl\.?\s*oz|pcs?|pc)\b",
    re.IGNORECASE,
)

# 영어 관사/전치사 — 단독 토큰으로만 제거
_STOP_WORDS = re.compile(r"\b(the|and|an)\b", re.IGNORECASE)


def normalize_for_matching(name: str) -> str:
    """
    OY Global 내부 유사도 비교용 정규화.

      1. 마케팅 설명 제거: 'with Ceramides...', '- Gently Exfoliate...' 등
      2. 용량 제거: '80mL', '45g' 등
      3. 카테고리·마케팅 수식어 제거
      4. 관사/전치사 제거: 'the', 'and', 'an'
      5. 소문자 변환 + 특수문자 → 공백
      6. 시리즈명 숫자 공백 통일: 'Atobarrier 365' → 'atobarrier365'
    """
    name = _SEP_DESC_PATTERN.sub("", name).strip()
    name = _CAPACITY_PATTERN.sub("", name)
    name = _FILLER_WORDS.sub("", name)
    name = _STOP_WORDS.sub("", name)
    name = name.lower()
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"([a-z])\s+(\d)", r"\1\2", name)
    name = re.sub(r"(\d)\s+([a-z])", r"\1\2", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def find_matching_product(
    conn: sqlite3.Connection,
    brand_id: str,
    product_name: str,
    threshold: int = MATCH_THRESHOLD,
    exclude_sephora: bool = False,
) -> str | None:
    """
    동일 브랜드 내에서 제품명 유사도가 threshold 이상인 기존 product_id 반환.
    없으면 None.

    알고리즘:
      - normalize_for_matching()으로 양쪽 이름 전처리
      - token_sort_ratio: 토큰 순서 무관 비교
      - token_set_ratio:  한쪽이 다른쪽의 부분집합인 경우 보완
      - 두 점수 중 높은 값 사용

    exclude_sephora=True: sephora_pid가 NULL인 row만 후보로 사용
    """
    if exclude_sephora:
        rows = conn.execute(
            "SELECT product_id, product_name_en FROM dim_product "
            "WHERE brand_id = ? AND sephora_pid IS NULL",
            (brand_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT product_id, product_name_en FROM dim_product WHERE brand_id = ?",
            (brand_id,),
        ).fetchall()

    norm_new   = normalize_for_matching(product_name)
    best_score = 0
    best_pid   = None
    best_name  = ""

    for row in rows:
        norm_existing = normalize_for_matching(row["product_name_en"])
        score = max(
            fuzz.token_sort_ratio(norm_new, norm_existing),
            fuzz.token_set_ratio(norm_new, norm_existing),
        )
        if score > best_score:
            best_score = score
            best_pid   = row["product_id"]
            best_name  = row["product_name_en"]

    if best_score >= threshold:
        logger.info(
            "  [매칭] '%s' → '%s' (%s, score=%d)",
            product_name, best_name, best_pid, best_score,
        )
        return best_pid

    logger.debug(
        "  [미매칭] '%s' (best_score=%d, best_candidate='%s')",
        product_name, best_score, best_name,
    )
    return None
