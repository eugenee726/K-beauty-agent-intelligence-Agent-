"""
brand_registry.py
──────────────────
브랜드 레지스트리 — dim_brand 를 단일 소스로 사용.

이전에는 아모레퍼시픽 브랜드 목록이 _ap_brands.py, sephora_tool.py,
tiktok_tool.py, youtube_tool.py 등에 하드코딩돼 있었다. 이 모듈은
dim_brand 에 수집용 메타데이터 컬럼을 추가하고, 브랜드 조회·매칭·자동 등록을
한 곳에서 처리한다.

dim_brand 추가 컬럼:
  company            모회사 (예: 'Amorepacific', 'APR')
  aliases            JSON 배열. 브랜드명 표기 변형 (예: ["etude house"])
  oy_global_brand_no OY Global brand-page brandNo (예: 'B00280')
  sephora_bv_name    Sephora BazaarVoice Brand.Name
  tiktok_hashtag     전용 TikTok 해시태그 (sns_tier='dedicated' 일 때 사용)
  youtube_query      전용 YouTube 검색어
  sns_tier           'dedicated' | 'keyword_only'
  is_tracked         1 = 정식 추적 브랜드, 0 = 랭킹 등에서 자동 관측된 브랜드
"""

import json
import re
import sqlite3

# 추가 컬럼 정의 (이름, 타입/기본값)
_EXTRA_COLUMNS: list[tuple[str, str]] = [
    ("company",            "TEXT"),
    ("aliases",            "TEXT"),
    ("oy_global_brand_no", "TEXT"),
    ("sephora_bv_name",    "TEXT"),
    ("tiktok_hashtag",     "TEXT"),
    ("youtube_query",      "TEXT"),
    ("sns_tier",           "TEXT DEFAULT 'keyword_only'"),
    ("is_tracked",         "INTEGER DEFAULT 1"),
]

# 기존 아모레퍼시픽 13개 브랜드의 수집 메타데이터.
# (이전 _ap_brands.py / sephora_tool.AP_BV_BRANDS 의 값을 옮겨 온 것)
_AP_SEED: dict[str, dict] = {
    "sulwhasoo":   {"sephora_bv_name": "Sulwhasoo"},
    "hera":        {"oy_global_brand_no": "B01392"},
    "primera":     {"oy_global_brand_no": "B01191"},
    "tata_harper": {"sephora_bv_name": "Tata Harper", "aliases": ["tataharper"]},
    "iope":        {"oy_global_brand_no": "B01210"},
    "aestura":     {"oy_global_brand_no": "B00214", "sephora_bv_name": "AESTURA"},
    "mamonde":     {"oy_global_brand_no": "B00234"},
    "hanyul":      {"oy_global_brand_no": "B00383"},
    "laneige":     {"oy_global_brand_no": "B00280", "sephora_bv_name": "LANEIGE"},
    "innisfree":   {"oy_global_brand_no": "B00519", "sephora_bv_name": "INNISFREE"},
    "cosrx":       {"oy_global_brand_no": "B00095"},
    "espoir":      {"oy_global_brand_no": "B00210"},
    "etude":       {"oy_global_brand_no": "B00288", "aliases": ["etude house"]},
}


def slugify(name: str) -> str:
    """브랜드 표시명 → brand_id 슬러그. 'Dr. Althea' → 'dr_althea'"""
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower().strip())
    return s.strip("_")


def ensure_brand_schema(conn: sqlite3.Connection) -> None:
    """
    dim_brand 에 추가 컬럼이 없으면 ALTER TABLE 로 추가하고,
    기존 AP 브랜드 행에 수집 메타데이터를 채운다. 여러 번 호출해도 안전하다.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(dim_brand)")}
    for name, decl in _EXTRA_COLUMNS:
        if name not in cols:
            conn.execute(f"ALTER TABLE dim_brand ADD COLUMN {name} {decl}")

    # AP 시드: 비어 있는 값만 채우므로(COALESCE) 매번 실행해도 사용자 수정값을 덮어쓰지 않는다.
    # schema.sql 이 시작 시마다 재실행되므로 새 DB 에서도 여기서 시드된다.
    for bid, meta in _AP_SEED.items():
        conn.execute(
            """UPDATE dim_brand SET
                   company            = COALESCE(company, 'Amorepacific'),
                   aliases            = COALESCE(aliases, ?),
                   oy_global_brand_no = COALESCE(oy_global_brand_no, ?),
                   sephora_bv_name    = COALESCE(sephora_bv_name, ?)
               WHERE brand_id = ?""",
            (
                json.dumps(meta["aliases"]) if meta.get("aliases") else None,
                meta.get("oy_global_brand_no"),
                meta.get("sephora_bv_name"),
                bid,
            ),
        )
    conn.commit()


def load_brands(conn: sqlite3.Connection, tracked_only: bool = False) -> list[dict]:
    """dim_brand 전체(또는 정식 추적 브랜드만)를 dict 목록으로 반환."""
    ensure_brand_schema(conn)
    sql = "SELECT * FROM dim_brand"
    if tracked_only:
        sql += " WHERE is_tracked = 1"
    cur = conn.execute(sql)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


class BrandMatcher:
    """
    브랜드 표기 → brand_id 매칭기.
    brand_id, brand_name_en, brand_name_kr, aliases 를 모두 이름 후보로 쓴다.
    sns_keywords 는 제품명·성분이 섞여 있어(예: 'snail mucin') 매칭에 쓰지 않는다.
    """

    def __init__(self, brands: list[dict]):
        self._exact: dict[str, str] = {}
        for b in brands:
            bid = b["brand_id"]
            names = {bid, bid.replace("_", " "), b.get("brand_name_en") or "",
                     b.get("brand_name_kr") or ""}
            try:
                names.update(json.loads(b.get("aliases") or "[]"))
            except (TypeError, ValueError):
                pass
            for n in names:
                key = n.lower().strip()
                if key:
                    self._exact[key] = bid
    def match_exact(self, brand_name: str) -> str | None:
        """브랜드 필드 값(예: 타일의 브랜드명)을 정확히 매칭. 표기 차이는 slugify 로 흡수."""
        key = (brand_name or "").lower().strip()
        return self._exact.get(key) or self._exact.get(slugify(key).replace("_", " "))


def register_observed_brand(conn: sqlite3.Connection, display_name: str) -> str | None:
    """
    처음 보는 브랜드를 dim_brand 에 is_tracked=0 으로 등록하고 brand_id 를 반환.
    dim_brand 에 행이 없으면 리포트·인사이트의 JOIN 에서 데이터가 빠지므로 필요하다.
    """
    bid = slugify(display_name)
    if not bid:
        return None
    conn.execute(
        """INSERT OR IGNORE INTO dim_brand
               (brand_id, brand_name_en, sns_keywords, sns_tier, is_tracked)
           VALUES (?, ?, ?, 'keyword_only', 0)""",
        (bid, display_name.strip(), json.dumps([display_name.strip().lower()])),
    )
    return bid
