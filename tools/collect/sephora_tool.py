"""
sephora_tool.py
────────────────
Sephora US 수집 툴 — 아모레퍼시픽 제품 전용.

역할: 랭킹 수집 X → 카탈로그 구축 + VOC 전용.
  · Sephora 랭킹은 리뷰수 proxy라 OY Global 판매 기반 랭킹과 성격이 달라
    트렌드 탐지 신호로 부적합 → 수집하지 않음.
  · Sephora는 트렌드 확정 후 영어 소비자 반응(VOC) 수집 용도로만 사용.

수집 항목:
  A) build_catalog()    — BV Products API로 AP 브랜드 전 제품 수집 → dim_product
                          + Playwright로 성분(key_ingredients) 스크랩
  B) fetch_reviews_bv() — BV Reviews API VOC 수집 (VocCollector에서 호출)

카탈로그 필드 매핑:
  BV Description    → key_benefits  (Skincare Concerns 파싱)
  BV ProductPageUrl → sephora_url   (실제 슬러그 포함 URL)
  BV CategoryId     → category_main / category_sub
  Playwright 스크랩 → key_ingredients (INCI 리스트 → known actives 필터링)

OY Global과 독립 카탈로그:
  Sephora 제품은 sephora_pid가 설정된 별도 row로 관리.
  OY 제품과 크로스 매핑하지 않음.
"""

import json
import re
import sqlite3
import logging
import time
import requests
from datetime import datetime, timezone

from tools.collect._product_utils import next_product_id

logger = logging.getLogger(__name__)

BV_PRODUCTS_URL = "https://api.bazaarvoice.com/data/products.json"
BV_REVIEWS_URL  = "https://api.bazaarvoice.com/data/reviews.json"
BV_PASSKEY      = "calXm2DyQVjcCy9agq85vmTJv5ELuuBCF2sdg4BnJzJus"

BV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.sephora.com/",
}

# Sephora US 실제 입점 AP 브랜드 (brand_id, BV 검색 키워드)
# COSRX는 Sephora US 미입점 확인됨
AP_BV_BRANDS: list[tuple[str, str]] = [
    ("aestura",     "AESTURA"),
    ("laneige",     "LANEIGE"),
    ("innisfree",   "INNISFREE"),
    ("sulwhasoo",   "Sulwhasoo"),
    ("tata_harper", "Tata Harper"),
]

# Sephora US PID 형식: 'P' + 숫자 (예: P521415)
SEPHORA_PID_PATTERN = re.compile(r'^P\d+$')

# 번들/세트/기프트 제품 제외 패턴 — 개별 단품만 수집
BUNDLE_NAME_PATTERN = re.compile(
    r'\b(set|kit|duo|trio|quartet|bundle|gift|wristlet|keychain|ornament)\b',
    re.IGNORECASE,
)

# BV CategoryId → (category_main, category_sub)
# 실제 BV API 응답 데이터에서 제품명으로 검증한 매핑
SEPHORA_CATEGORY_MAP: dict[str, tuple[str, str | None]] = {
    # ── Skincare ──────────────────────────────────
    "cat60097":  ("skincare", "moisturizer"),   # Water Bank Blue Hyaluronic Moisturizer
    "cat60103":  ("skincare", "serum"),          # Perfect Renew Signature Serum, Bouncy & Firm Serum
    "cat60099":  ("skincare", "cleanser"),       # Rice Foaming Gel Cleanser, Gel Cleanser
    "cat920041": ("skincare", "mask"),           # Sleeping Mask, Peel-Off Mask
    "cat60109":  ("skincare", "lip"),            # Glaze Craze Lip Serum, Plumping Lip Treatment
    "cat60107":  ("skincare", "eye_cream"),      # Vitamin C Eye Serum, Green Tea Eye Cream
    "cat60101":  ("skincare", "toner"),          # Cream Skin Milky Toner, Retinol Green Tea Toner
    "cat920033": ("skincare", "sunscreen"),      # Hydro UV Defense Sunscreen
    "cat60113":  ("skincare", "sunscreen"),      # UV Invisible Sunscreen Stick/Lotion
    "cat1210035":("skincare", "toner"),          # Green Tea Ceramide Milk Toner
    "cat1230034":("skincare", "moisturizer"),    # Green Tea Ceramide Cream
    "cat1440040":("skincare", "mask"),           # Retinol Sheet Mask, Activating Sheet Mask
    "cat1170031":("skincare", "treatment"),      # Microneedle Patch, Spot Solution
    "cat1600036":("skincare", "mask"),           # Eye & Lip Sleeping Mask
    "cat1600043":("skincare", "toner"),          # Cream Skin Mist Pump
    "cat150006":  ("skincare", None),            # mixed (clay mask, cleanser, lip balm)
    "cat60165":  ("skincare", "body_serum"),     # Resurfacing Body Serum
    "cat60163":  ("skincare", "body_cream"),     # Redefining Body Balm
    "cat60182":  ("skincare", "body_lotion"),    # ATOBARRIER365 Body Lotion
    # ── Makeup ────────────────────────────────────
    "cat1760033":("makeup", "setting_powder"),   # Neo Blurring Loose Finishing Powder
    "cat60008":  ("makeup", "setting_powder"),   # No Sebum Matte Mineral Blurring Powder
    "cat60018":  ("makeup", "blush"),            # Very Bronzing Cheek Tint
    "cat60020":  ("makeup", "highlighter"),      # Very Highlighting Cheek Tint
    "cat60055":  ("makeup", "lip"),              # Volumizing Lip & Cheek Tint
    "cat60059":  ("makeup", "lip"),              # Lip Icons
    # ── Sets / Kits (category_sub=None) ──────────
    "cat60105":  ("skincare", None),             # Set/Kit/Gift (대분류)
    "cat180018": ("skincare", None),             # Makeup & Skincare Value Set
    "cat1830034":("skincare", None),             # Minis
    "cat1910031":("skincare", None),             # Discovery/Best Seller Sets
    "cat1940030":("skincare", None),             # Trial Kits
    "cat1550037":("skincare", None),             # Hydrate & Protect Set
    "cat60143":  ("skincare", None),             # Limited Edition Sets
    "cat60146":  ("skincare", None),             # Sephora Favorites
    # ── Body / Fragrance ──────────────────────────
    "cat1200068":("body", None),                 # Aromatic Love Potion
    "cat2420033":("body", None),                 # Aromatic Bedtime Treatment
    # ── Hair ──────────────────────────────────────
    "cat60088":  ("hair", None),
    # ── Oil ───────────────────────────────────────
    "cat1960033":("skincare", "oil"),            # Retinoic Nutrient Face Oil Mini
    "cat1120031":("skincare", "oil"),            # Beautifying Brightening Face Oil
    # ── Makeup (기타) ─────────────────────────────
    "cat780035": ("makeup", "eye"),              # Concentrated Ginseng Eye Cream Sample (makeup shelf)
    "cat60165":  ("skincare", "body_serum"),
}

# 알려진 K-beauty 핵심 성분 사전 (INCI 리스트에서 매칭 시 추출)
KNOWN_ACTIVES: frozenset[str] = frozenset({
    "niacinamide", "hyaluronic acid", "sodium hyaluronate",
    "ceramide", "ceramide np", "ceramide ap", "ceramide eop",
    "retinol", "retinyl palmitate", "retinal",
    "ascorbic acid", "vitamin c", "sodium ascorbyl phosphate",
    "centella asiatica", "asiaticoside", "madecassoside",
    "snail secretion filtrate", "saccharomyces ferment filtrate",
    "propolis", "bee venom",
    "tranexamic acid", "azelaic acid", "bakuchiol",
    "adenosine", "arbutin", "kojic acid",
    "salicylic acid", "glycolic acid", "lactic acid",
    "mandelic acid", "phytic acid",
    "panthenol", "allantoin", "bisabolol",
    "beta-glucan", "oat extract",
    "galactomyces ferment filtrate", "ferment filtrate",
    "mugwort extract", "artemisia vulgaris",
    "panax ginseng", "ginseng root extract",
    "camellia sinensis", "green tea extract",
    "oryza sativa", "rice extract",
    "peptide", "palmitoyl pentapeptide", "acetyl hexapeptide",
    "copper tripeptide",
    "squalane", "jojoba", "simmondsia chinensis",
    "tocopherol", "vitamin e",
    "zinc oxide", "titanium dioxide",
    "collagen", "elastin",
    "resveratrol", "ferulic acid",
    "tea tree", "melaleuca alternifolia",
    "alpha-arbutin", "licorice extract",
})


class SephoraTool:
    PLATFORM_ID = "sephora_us"

    def __init__(self, db_path: str, headless: bool = True):
        self.db_path  = db_path
        self.headless = headless

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _current_week(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%G-W%V")

    def _build_product_map(self) -> dict[str, str]:
        """sephora_pid → product_id 맵 반환."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT product_id, sephora_pid FROM dim_product "
                "WHERE sephora_pid IS NOT NULL"
            ).fetchall()
        return {r["sephora_pid"]: r["product_id"] for r in rows}

    # ──────────────────────────────────────────
    # BazaarVoice Products API
    # ──────────────────────────────────────────
    def _fetch_bv_products(self, brand_name: str,
                           page_size: int = 100) -> list[dict]:
        """
        BazaarVoice Products API로 브랜드 현재 판매 중인 제품 전체 조회.
        페이지네이션으로 100개 제한 없이 전수 수집.

        필터링 4단계:
          ① Brand.Name 일치 (대소문자 무시) — 타 브랜드 오염 제거
          ② PID 형식 P+숫자                — Sephora US 전용 제품만 유지
          ③ Active=true & Disabled=false   — 현재 판매 중인 제품만 유지
          ④ 번들/세트 제외
        """
        brand_lower = brand_name.lower()
        all_raw:      list[dict] = []
        offset = 0

        try:
            while True:
                params = {
                    "Search":     brand_name,
                    "Limit":      page_size,
                    "Offset":     offset,
                    "passkey":    BV_PASSKEY,
                    "apiversion": "5.4",
                }
                resp = requests.get(BV_PRODUCTS_URL, params=params,
                                    headers=BV_HEADERS, timeout=30)
                resp.raise_for_status()
                data         = resp.json()
                page_results = data.get("Results", [])
                total        = data.get("TotalResults", 0)
                all_raw.extend(page_results)

                offset += page_size
                if offset >= total or not page_results:
                    break

        except Exception as e:
            logger.warning(f"BV Products 오류 ({brand_name}): {e}")
            return []

        results = [
            r for r in all_raw
            if (r.get("Brand") or {}).get("Name", "").lower() == brand_lower
            and SEPHORA_PID_PATTERN.match(r.get("Id", ""))
            and r.get("Active") is True
            and not r.get("Disabled", False)
            and not BUNDLE_NAME_PATTERN.search(r.get("Name", ""))
        ]

        logger.info(
            f"  BV Products [{brand_name}]: "
            f"전체 {len(all_raw)}개 → 단품 활성 {len(results)}개"
        )
        return results

    # ──────────────────────────────────────────
    # BV Description → key_benefits 파싱
    # ──────────────────────────────────────────
    def _parse_benefits(self, description: str) -> str | None:
        """
        BV Description 텍스트에서 key_benefits 추출.

        "Skincare Concerns: Dryness, Fine Lines and Wrinkles, Loss of Firmness"
        → ["dryness", "fine lines and wrinkles", "loss of firmness"]
        """
        if not description:
            return None

        benefits: list[str] = []

        # Skincare Concerns 파싱 — 마침표/줄바꿈/다음 섹션 헤더에서 종료
        m = re.search(
            r'Skincare Concerns?:\s*(.+?)(?=[.\n\r]|Formulation:|Highlighted|What else)',
            description, re.IGNORECASE | re.DOTALL
        )
        if m:
            raw = m.group(1).strip().rstrip('.')
            # "and " 접속사 처리: "Fine Lines and Wrinkles" 은 하나의 항목 유지
            items = re.split(r',\s+(?!and\b)', raw)
            for item in items:
                cleaned = item.strip().rstrip('.').lower()
                if cleaned and len(cleaned) < 50:  # 너무 긴 항목 제외
                    benefits.append(cleaned)

        # What it is 첫 문장도 참고 (없어도 무방)
        if not benefits:
            m2 = re.search(
                r'What it is:\s*([^.\n\r]+)',
                description, re.IGNORECASE
            )
            if m2:
                sentence = m2.group(1).strip().lower()
                # 마케팅 언어에서 기능 키워드 추출
                for kw in ["hydrating", "brightening", "firming", "soothing",
                           "anti-aging", "exfoliating", "cleansing", "calming"]:
                    if kw in sentence:
                        benefits.append(kw)

        return json.dumps(benefits[:6]) if benefits else None

    def _parse_ingredients_from_description(self, description: str) -> str | None:
        """
        BV Description 'Highlighted Ingredients' 섹션 → key_ingredients JSON.

        형식: "Highlighted Ingredients: - Name: desc. - Name: desc. ..."
        → 성분명만 추출 → KNOWN_ACTIVES 매칭 우선, 미매칭은 원문 그대로 저장.
        """
        if not description:
            return None

        # "Highlighted Ingredients:" 섹션 추출 (다음 섹션 헤더에서 종료)
        m = re.search(
            r'Highlighted Ingredients?:\s*(.+?)(?=What Else|Ingredient Callouts|$)',
            description, re.IGNORECASE | re.DOTALL
        )
        if not m:
            return None

        raw_section = m.group(1).strip()

        # "- IngredientName (괄호 설명): 효능 설명." 패턴에서 이름 추출
        bullet_names = re.findall(r'-\s*([^:\n]{2,60}):', raw_section)

        ingredients: list[str] = []
        seen: set[str] = set()

        for raw_name in bullet_names:
            raw_name = raw_name.strip()

            # 괄호 내용도 별도 검사 (복합 성분명에 known active가 숨어있는 경우)
            # 예) "Rice-inamide Complex (Rice Complex and Niacinamide)"
            full_text = raw_name.lower()
            parenthetical = re.findall(r'\(([^)]+)\)', full_text)
            check_texts = [full_text] + [p.lower() for p in parenthetical]

            matched = False
            for text in check_texts:
                for active in KNOWN_ACTIVES:
                    if active in text and active not in seen:
                        ingredients.append(active)
                        seen.add(active)
                        matched = True

            # KNOWN_ACTIVES 미매칭 → 괄호 제거 후 원문 그대로 저장
            if not matched:
                clean = re.sub(r'\s*\([^)]*\)', '', raw_name).strip().lower()
                if clean and len(clean) <= 50 and clean not in seen:
                    ingredients.append(clean)
                    seen.add(clean)

        return json.dumps(ingredients[:10]) if ingredients else None

    # ──────────────────────────────────────────
    # BV CategoryId → category_main / sub
    # ──────────────────────────────────────────
    def _map_category(self, category_id: str) -> tuple[str | None, str | None]:
        """
        BV CategoryId → (category_main, category_sub).

        매핑 테이블은 실제 BV API 응답 데이터 기반으로 검증됨.
        미매핑 ID는 AP 브랜드 특성상 skincare 기본값 적용.
        """
        if not category_id:
            return (None, None)
        result = SEPHORA_CATEGORY_MAP.get(category_id)
        if result:
            return result
        logger.debug(f"  미매핑 CategoryId: {category_id} → skincare 기본값")
        return ("skincare", None)

    # ──────────────────────────────────────────
    # Playwright — 성분 스크랩
    # ──────────────────────────────────────────
    def _extract_ingredients_from_page(self, page) -> str | None:
        """
        Playwright 페이지 객체에서 Ingredients 원문 텍스트 추출.

        전략 1: #ingredients > div  (실제 Sephora DOM 셀렉터 — 검증됨)
        전략 2: HTML 원문 Water/Aqua 패턴 정규식 (fallback)
        """
        # ─── 전략 1: 실제 Sephora DOM 셀렉터 ───
        try:
            el = page.locator("#ingredients > div").first
            if el.count() > 0:
                text = el.inner_text(timeout=3000)
                if text and len(text) > 30:
                    return text
        except Exception:
            pass

        # ─── 전략 2: HTML 원문 패턴 (fallback) ───
        try:
            content = page.content()
            # Water/Aqua로 시작하는 INCI 리스트 패턴
            m = re.search(
                r'(?:Water/Aqua/Eau|Aqua/Water/Eau|Water \(Aqua\))'
                r'[A-Za-z0-9 ,/\-\(\)\.\+\*]{50,}',
                content,
            )
            if m:
                import html as html_module
                return html_module.unescape(m.group(0))
        except Exception:
            pass

        return None

    def _parse_ingredients(self, raw_text: str) -> list[str]:
        """
        INCI 원문 → key actives 리스트.

        KNOWN_ACTIVES 사전과 매칭되는 성분만 추출.
        순서는 INCI 등장 순서 유지 (고농도 → 저농도).
        """
        if not raw_text:
            return []

        # 인코딩 이스케이프 정리
        raw_text = raw_text.replace("\\n", " ").replace("\\t", " ")
        raw_text = re.sub(r'\\u[0-9a-fA-F]{4}', ' ', raw_text)

        # 콤마 구분 → 소문자 항목 리스트
        items = [i.strip().lower() for i in raw_text.split(',') if i.strip()]

        matched: list[str] = []
        seen: set[str] = set()

        for item in items:
            for active in KNOWN_ACTIVES:
                if active in item and active not in seen:
                    matched.append(active)
                    seen.add(active)
                    break

        return matched[:15]  # 최대 15개

    def _scrape_ingredients_batch(
        self,
        products: list[tuple[str, str]],
    ) -> dict[str, str]:
        """
        Playwright 배치 스크랩 — 여러 제품 페이지 성분 수집.

        Args:
            products: [(sephora_pid, sephora_url), ...]

        Returns:
            {sephora_pid: key_ingredients_json}
        """
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.error("playwright 미설치 — pip install playwright 후 실행")
            return {}

        results: dict[str, str] = {}
        logger.info(f"Playwright 성분 스크랩 시작: {len(products)}개 제품")

        try:
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=self.headless)
                ctx = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 800},
                    locale="en-US",
                )

                for idx, (pid, url) in enumerate(products, 1):
                    page = ctx.new_page()
                    try:
                        logger.info(f"  [{idx}/{len(products)}] {pid} 스크랩 중...")
                        page.goto(url, wait_until="domcontentloaded", timeout=25000)
                        page.wait_for_timeout(2500)

                        # Ingredients 아코디언 클릭 (collapsed 상태일 경우)
                        try:
                            btn = page.get_by_role(
                                "button",
                                name=re.compile(r"^ingredients$", re.IGNORECASE)
                            ).first
                            if btn.count() > 0 and btn.is_visible():
                                btn.click()
                                page.wait_for_timeout(600)
                        except Exception:
                            pass

                        raw = self._extract_ingredients_from_page(page)
                        if raw:
                            parsed = self._parse_ingredients(raw)
                            if parsed:
                                results[pid] = json.dumps(parsed)
                                logger.info(
                                    f"  [{pid}] 성분 {len(parsed)}개: "
                                    f"{', '.join(parsed[:5])}..."
                                )
                            else:
                                logger.debug(f"  [{pid}] 성분 텍스트 발견했으나 파싱 결과 없음")
                        else:
                            logger.debug(f"  [{pid}] 성분 텍스트 미발견")

                    except Exception as e:
                        logger.warning(f"  [{pid}] 스크랩 실패: {e}")
                    finally:
                        try:
                            page.close()
                        except Exception:
                            pass

                    time.sleep(1.0)  # rate limiting

                browser.close()

        except Exception as e:
            logger.error(f"Playwright 초기화/실행 실패: {e}")

        logger.info(f"Playwright 스크랩 완료: {len(results)}/{len(products)}개 성공")
        return results

    # ──────────────────────────────────────────
    # BazaarVoice Reviews API
    # ──────────────────────────────────────────
    def fetch_reviews_bv(self, sephora_pid: str,
                         limit: int = 50) -> list[dict]:
        """BazaarVoice Reviews API로 제품 리뷰 수집."""
        params = {
            "Filter":     [f"ProductId:{sephora_pid}", "contentlocale:en*"],
            "Sort":       "SubmissionTime:desc",
            "Limit":      limit,
            "Include":    "Products,Comments",
            "Stats":      "Reviews",
            "passkey":    BV_PASSKEY,
            "apiversion": "5.4",
            "Locale":     "en_US",
        }
        try:
            resp = requests.get(BV_REVIEWS_URL, params=params,
                                headers=BV_HEADERS, timeout=30)
            resp.raise_for_status()
            reviews = []
            for r in resp.json().get("Results", []):
                dt_str = r.get("SubmissionTime", "")
                try:
                    dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                    submission_time = dt.strftime("%Y/%m/%d")
                except Exception:
                    submission_time = dt_str
                text = r.get("ReviewText", "")
                if not text:
                    continue
                reviews.append({
                    "rating":          float(r["Rating"]) if r.get("Rating") else None,
                    "text":            text,
                    "helpful":         r.get("TotalPositiveFeedbackCount", 0),
                    "submission_time": submission_time,
                })
            logger.info(f"  BV Reviews [{sephora_pid}]: {len(reviews)}개")
            return reviews
        except Exception as e:
            logger.warning(f"BV Reviews 오류 ({sephora_pid}): {e}")
            return []

    # ──────────────────────────────────────────
    # A. 카탈로그 구축 (월 1회)
    # ──────────────────────────────────────────
    def build_catalog(self, scrape_ingredients: bool = True) -> dict:
        """
        AP 5개 브랜드 Sephora 전체 제품 → dim_product.

        단계:
          1) BazaarVoice Products API → 제품 목록 수집
             - Description → key_benefits
             - ProductPageUrl → sephora_url
             - CategoryId → category_main / category_sub
          2) dim_product INSERT (신규) / UPDATE (기존 필드 보완)
          3) Playwright → key_ingredients 스크랩 후 UPDATE
             (scrape_ingredients=False 시 스킵)

        sephora_pid 기준으로 중복 체크:
          - 신규: INSERT
          - 기존: key_benefits / category_main / category_sub / sephora_url 만 UPDATE
                  (product_name_en, brand_id 는 덮어쓰지 않음)
        """
        logger.info("Sephora 카탈로그 구축 시작 (BazaarVoice API)")
        all_products: list[dict] = []

        for brand_id, bv_brand_name in AP_BV_BRANDS:
            results = self._fetch_bv_products(bv_brand_name)
            for r in results:
                sephora_pid = r.get("Id", "")
                if not sephora_pid:
                    continue
                name = r.get("Name", "").strip()
                if not name:
                    continue

                description   = r.get("Description", "") or ""
                category_id   = r.get("CategoryId", "") or ""
                product_page  = r.get("ProductPageUrl", "") or ""

                # sephora_url: ProductPageUrl 우선, 없으면 pid 기반 구성
                sephora_url = (
                    product_page
                    if product_page.startswith("https://www.sephora.com/")
                    else f"https://www.sephora.com/product/{sephora_pid}"
                )

                category_main, category_sub = self._map_category(category_id)
                key_benefits     = self._parse_benefits(description)
                key_ingredients  = self._parse_ingredients_from_description(description)

                all_products.append({
                    "brand_id":        brand_id,
                    "product_name_en": name,
                    "sephora_pid":     sephora_pid,
                    "sephora_url":     sephora_url,
                    "category_main":   category_main,
                    "category_sub":    category_sub,
                    "key_benefits":    key_benefits,
                    "key_ingredients": key_ingredients,
                })

        # ── DB 저장 (INSERT 신규 / UPDATE 기존) ──
        total_new    = 0
        total_update = 0
        total_skip   = 0

        with self._conn() as conn:
            brand_stats: dict[str, dict] = {}

            for p in all_products:
                brand_id    = p["brand_id"]
                sephora_pid = p["sephora_pid"]
                stats = brand_stats.setdefault(brand_id, {"new": 0, "update": 0, "skip": 0})

                try:
                    row = conn.execute(
                        "SELECT product_id FROM dim_product WHERE sephora_pid = ?",
                        (sephora_pid,)
                    ).fetchone()

                    if row:
                        # 기존 제품 — 메타 필드만 보완 (이름/브랜드 변경 없음)
                        conn.execute("""
                            UPDATE dim_product
                               SET sephora_url    = COALESCE(NULLIF(?, ''), sephora_url),
                                   category_main  = COALESCE(?, category_main),
                                   category_sub   = COALESCE(?, category_sub),
                                   key_benefits   = COALESCE(?, key_benefits),
                                   key_ingredients= COALESCE(?, key_ingredients)
                             WHERE sephora_pid = ?
                        """, (
                            p["sephora_url"],
                            p["category_main"],
                            p["category_sub"],
                            p["key_benefits"],
                            p["key_ingredients"],
                            sephora_pid,
                        ))
                        if conn.execute("SELECT changes()").fetchone()[0]:
                            stats["update"] += 1
                            total_update += 1
                        else:
                            stats["skip"] += 1
                            total_skip += 1

                    else:
                        # 신규 제품 — INSERT
                        pid = next_product_id(conn)
                        conn.execute("""
                            INSERT OR IGNORE INTO dim_product
                                (product_id, brand_id, product_name_en,
                                 sephora_pid, sephora_url,
                                 category_main, category_sub,
                                 key_benefits, key_ingredients)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """, (
                            pid,
                            p["brand_id"],
                            p["product_name_en"],
                            p["sephora_pid"],
                            p["sephora_url"],
                            p["category_main"],
                            p["category_sub"],
                            p["key_benefits"],
                            p["key_ingredients"],
                        ))
                        if conn.execute("SELECT changes()").fetchone()[0]:
                            stats["new"] += 1
                            total_new += 1

                except Exception as e:
                    logger.debug("Sephora 카탈로그 등록 실패: %s", e)

            for bid, s in brand_stats.items():
                logger.info(
                    "  [%s] 신규 %d개 / 업데이트 %d개 / 스킵 %d개",
                    bid, s["new"], s["update"], s["skip"],
                )

        logger.info(
            "Sephora BV 카탈로그 완료: 전체 %d개 / 신규 %d개 / 업데이트 %d개",
            len(all_products), total_new, total_update,
        )

        # ── Playwright 성분 스크랩 ──
        ingr_count = 0
        if scrape_ingredients and all_products:
            # key_ingredients 가 없는 제품만 스크랩 (기존 데이터 유지)
            with self._conn() as conn:
                rows = conn.execute("""
                    SELECT product_id, sephora_pid, sephora_url
                    FROM dim_product
                    WHERE sephora_pid IS NOT NULL
                      AND (key_ingredients IS NULL OR key_ingredients = '[]')
                      AND sephora_url IS NOT NULL
                """).fetchall()

            to_scrape = [(r["sephora_pid"], r["sephora_url"]) for r in rows]
            logger.info(f"성분 스크랩 대상: {len(to_scrape)}개 제품")

            if to_scrape:
                ingr_map = self._scrape_ingredients_batch(to_scrape)

                # UPDATE key_ingredients
                with self._conn() as conn:
                    for pid_bv, ingr_json in ingr_map.items():
                        conn.execute("""
                            UPDATE dim_product
                               SET key_ingredients = ?
                             WHERE sephora_pid = ?
                        """, (ingr_json, pid_bv))
                ingr_count = len(ingr_map)
                logger.info(f"Playwright 성분 저장 완료: {ingr_count}개 제품")

        return {
            "total":        len(all_products),
            "new":          total_new,
            "updated":      total_update,
            "ingredients":  ingr_count,
        }
