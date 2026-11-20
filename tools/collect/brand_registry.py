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
  oy_global_brand_no OY Global brand-page brandNo (예: 'B00280') — 옛 주소, 새 주소로 302 이동됨
  oy_brand_slug      OY Global 새 브랜드 주소 /global/brands/<slug> (있으면 이쪽을 우선 사용)
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
    ("oy_brand_slug",      "TEXT"),   # OY Global 새 브랜드 주소 /global/brands/<slug>
    ("sephora_bv_name",    "TEXT"),
    ("tiktok_hashtag",     "TEXT"),
    ("youtube_query",      "TEXT"),
    ("sns_tier",           "TEXT DEFAULT 'keyword_only'"),
    ("is_tracked",         "INTEGER DEFAULT 1"),
]

# ── 추적 브랜드 시드 ─────────────────────────────────────────────
# name: 표시명 / company: 모회사(확인된 것만, 모르면 생략) / oy: OY Global brandNo
# slug: OY Global 새 브랜드 주소 이름 (/global/brands/<slug>) — brandNo 가 없는 브랜드용
# bv: Sephora BazaarVoice Brand.Name(Sephora US 입점 확인된 것만)
# tag/query: 전용 TikTok 해시태그·YouTube 검색어 → 있으면 sns_tier='dedicated'
# aliases: 표기 변형(브랜드 매칭·SNS 키워드 탐지에 사용)
#
# 2026-11-20 확정: 기존 아모레퍼시픽 13개 + W47 OY 랭킹 기반 30개.
# OY brandNo 는 상품 상세 페이지에서, Sephora 이름은 BV API 검색으로 확인했다.
_AP = "Amorepacific"
_SEED: dict[str, dict] = {
    # 아모레퍼시픽 (기존)
    "sulwhasoo":   {"company": _AP, "bv": "Sulwhasoo", "tag": "sulwhasoo", "query": "sulwhasoo review"},
    "hera":        {"company": _AP, "oy": "B01392", "tag": "hera", "query": "hera skincare"},
    "primera":     {"company": _AP, "oy": "B01191", "tag": "primera", "query": "primera skincare"},
    "tata_harper": {"company": _AP, "bv": "Tata Harper", "aliases": ["tataharper"], "tag": "tataharper", "query": "tata harper review"},
    "iope":        {"company": _AP, "oy": "B01210", "tag": "iope", "query": "iope skincare"},
    "aestura":     {"company": _AP, "oy": "B00214", "bv": "AESTURA", "tag": "aestura", "query": "aestura skincare"},
    "mamonde":     {"company": _AP, "oy": "B00234", "tag": "mamonde", "query": "mamonde review"},
    "hanyul":      {"company": _AP, "oy": "B00383", "tag": "hanyul", "query": "hanyul skincare"},
    "laneige":     {"company": _AP, "oy": "B00280", "bv": "LANEIGE", "tag": "laneige", "query": "laneige review"},
    "innisfree":   {"company": _AP, "oy": "B00519", "bv": "INNISFREE", "tag": "innisfree", "query": "innisfree review"},
    "cosrx":       {"company": _AP, "oy": "B00095", "tag": "cosrx", "query": "cosrx review"},
    "espoir":      {"company": _AP, "oy": "B00210", "tag": "espoir", "query": "espoir review"},
    "etude":       {"company": _AP, "oy": "B00288", "aliases": ["etude house"], "tag": "etude", "query": "etude house review"},
    # 신규 — 전용 SNS 수집 15개
    "mediheal":         {"name": "MEDIHEAL", "company": "L&P Cosmetic", "oy": "B00051", "tag": "mediheal", "query": "mediheal review"},
    "numbuzin":         {"name": "numbuzin", "oy": "B00423", "tag": "numbuzin", "query": "numbuzin review"},
    "torriden":         {"name": "Torriden", "oy": "B00256", "bv": "Torriden", "tag": "torriden", "query": "torriden review"},
    "anua":             {"name": "Anua", "oy": "B00399", "bv": "Anua", "tag": "anua", "query": "anua review"},
    "round_lab":        {"name": "ROUND LAB", "oy": "B00244", "aliases": ["round lab", "roundlab"], "tag": "roundlab", "query": "round lab review"},
    "skin1004":         {"name": "SKIN1004", "company": "Craver Corporation", "oy": "B00568", "tag": "skin1004", "query": "skin1004 review"},
    "beauty_of_joseon": {"name": "Beauty of Joseon", "company": "Goodai Global", "oy": "B00664", "bv": "Beauty of Joseon", "tag": "beautyofjoseon", "query": "beauty of joseon review"},
    "medicube":         {"name": "medicube", "company": "APR", "oy": "B00177", "tag": "medicube", "query": "medicube review"},
    "dr_jart":          {"name": "Dr.Jart+", "company": "Estée Lauder Companies", "oy": "B00061", "bv": "Dr. Jart+", "aliases": ["dr jart", "dr. jart", "dr.jart"], "tag": "drjart", "query": "dr jart review"},
    "tirtir":           {"name": "TIRTIR", "company": "Goodai Global", "oy": "B00973", "tag": "tirtir", "query": "tirtir review"},
    "biodance":         {"name": "BIODANCE", "oy": "B00975", "bv": "Biodance", "tag": "biodance", "query": "biodance review"},
    "rejuran":          {"name": "REJURAN", "company": "PharmaResearch", "oy": "B00604", "bv": "Rejuran", "tag": "rejuran", "query": "rejuran review"},
    "abib":             {"name": "Abib", "oy": "B00132", "bv": "Abib", "tag": "abib", "query": "abib review"},
    "rom_nd":           {"name": "rom&nd", "oy": "B00155", "aliases": ["romand", "rom nd"], "tag": "romand", "query": "romand review"},
    "d_alba":           {"name": "d'Alba", "company": "d'Alba Global", "oy": "B00246", "aliases": ["dalba", "d alba"], "tag": "dalba", "query": "d'alba review"},
    # 신규 — 키워드 탐지만 15개
    "bioheal_boh":    {"name": "BIOHEAL BOH", "company": "CJ Olive Young", "oy": "B00099", "bv": "BIOHEAL BOH", "aliases": ["bioheal boh", "bioheal"]},
    "wakemake":       {"name": "wakemake", "company": "CJ Olive Young", "oy": "B00065"},
    "celimax":        {"name": "celimax", "oy": "B00285"},
    "beplain":        {"name": "beplain", "oy": "B00259", "bv": "beplain"},
    "wellage":        {"name": "WELLAGE", "company": "Hugel", "oy": "B00185", "bv": "WELLAGE"},
    "clio":           {"name": "CLIO", "company": "CLIO Cosmetics", "oy": "B00064", "aliases": ["clio cosmetics"]},
    "centellian24":   {"name": "Centellian24", "company": "Dongkook Pharmaceutical", "oy": "B00270", "aliases": ["centellian 24"]},
    "dr_althea":      {"name": "Dr. Althea", "oy": "B00157", "aliases": ["dr althea", "dr.althea"]},
    "vt":             {"name": "VT", "oy": "B00168", "aliases": ["vt cosmetics", "vt reedle shot", "reedle shot"]},
    "purito_seoul":   {"name": "Purito Seoul", "oy": "B01347", "aliases": ["purito"]},
    "amuse":          {"name": "AMUSE", "oy": "B00505", "aliases": ["amuse makeup", "amuse jel fit tint"]},
    "isntree":        {"name": "Isntree", "oy": "B00160"},
    "mixsoon":        {"name": "mixsoon", "slug": "mixsoon"},
    "medicube_age_r": {"name": "MEDICUBE AGE-R", "company": "APR", "oy": "B01121", "aliases": ["age-r", "age r", "ager booster"]},
    "aprilskin":      {"name": "APRILSKIN", "company": "APR", "slug": "april-skin", "aliases": ["april skin"]},
}

# SNS 키워드 탐지에 쓰기에는 너무 짧거나 일반 영어 단어와 겹치는 표시명 (별칭만 사용)
_AMBIGUOUS_NAMES = {"vt", "amuse"}

# 시드 버전. 올리면 is_tracked 를 시드 기준으로 다시 맞춘다 (한 번만 적용).
_SEED_VERSION = 2


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

    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta_registry (key TEXT PRIMARY KEY, value TEXT)"
    )
    row = conn.execute("SELECT value FROM meta_registry WHERE key = 'brand_seed_version'").fetchone()
    applied = int(row[0]) if row else 0

    # 시드: 빈 값만 채우므로(COALESCE) 매번 실행해도 사용자가 고친 값을 덮어쓰지 않는다.
    # schema.sql 이 시작 시마다 재실행되므로 새 DB 에서도 여기서 시드된다.
    for bid, m in _SEED.items():
        name = m.get("name") or bid
        conn.execute(
            """INSERT OR IGNORE INTO dim_brand (brand_id, brand_name_en, sns_keywords, is_tracked)
               VALUES (?, ?, ?, 1)""",
            (bid, name, json.dumps([name.lower(), *m.get("aliases", [])])),
        )
        conn.execute(
            """UPDATE dim_brand SET
                   company            = COALESCE(company, ?),
                   aliases            = COALESCE(aliases, ?),
                   oy_global_brand_no = COALESCE(oy_global_brand_no, ?),
                   oy_brand_slug      = COALESCE(oy_brand_slug, ?),
                   sephora_bv_name    = COALESCE(sephora_bv_name, ?),
                   tiktok_hashtag     = COALESCE(tiktok_hashtag, ?),
                   youtube_query      = COALESCE(youtube_query, ?)
               WHERE brand_id = ?""",
            (
                m.get("company"),
                json.dumps(m["aliases"]) if m.get("aliases") else None,
                m.get("oy"), m.get("slug"), m.get("bv"), m.get("tag"), m.get("query"),
                bid,
            ),
        )
        if applied < _SEED_VERSION:
            # 랭킹 수집이 is_tracked=0 으로 자동 등록해 둔 브랜드를 정식 추적으로 전환 (1회만)
            conn.execute(
                "UPDATE dim_brand SET is_tracked = 1, sns_tier = ? WHERE brand_id = ?",
                ("dedicated" if m.get("tag") else "keyword_only", bid),
            )
    if applied < _SEED_VERSION:
        conn.execute(
            "INSERT OR REPLACE INTO meta_registry (key, value) VALUES ('brand_seed_version', ?)",
            (str(_SEED_VERSION),),
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


def brand_terms(brands: list[dict]) -> set[str]:
    """
    SNS 캡션에서 브랜드를 탐지할 소문자 용어 집합 (표시명 + 별칭).
    너무 짧거나 일반 단어와 겹치는 표시명은 빼고 별칭만 쓴다.
    """
    terms: set[str] = set()
    for b in brands:
        name = (b.get("brand_name_en") or "").lower().strip()
        if name and len(name) > 2 and name not in _AMBIGUOUS_NAMES:
            terms.add(name)
        try:
            terms.update(a.lower().strip() for a in json.loads(b.get("aliases") or "[]"))
        except (TypeError, ValueError):
            pass
    return {t for t in terms if t}


def dedicated_sns_targets(conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """전용 수집 대상 (TikTok 해시태그 목록, YouTube 검색어 목록). 추적 + dedicated 브랜드만."""
    brands = [b for b in load_brands(conn, tracked_only=True) if b.get("sns_tier") == "dedicated"]
    tags = sorted({b["tiktok_hashtag"] for b in brands if b.get("tiktok_hashtag")})
    queries = sorted({b["youtube_query"] for b in brands if b.get("youtube_query")})
    return tags, queries


def set_oy_brand_no(conn: sqlite3.Connection, brand_id: str, brand_no: str) -> None:
    """상품 상세 페이지에서 알아낸 OY brandNo 를 비어 있을 때만 기록."""
    if brand_id and brand_no:
        conn.execute(
            "UPDATE dim_brand SET oy_global_brand_no = COALESCE(oy_global_brand_no, ?) WHERE brand_id = ?",
            (brand_no, brand_id),
        )


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
