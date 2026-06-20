"""
oy_global_tool.py
──────────────────
OliveYoung Global (global.oliveyoung.com) 수집 툴 — 아모레퍼시픽 제품 전용.

수집 항목:
  A) 제품 카탈로그 구축 (build_catalog) — 일회성 or 월 1회
     URL: /display/page/brand-page?brandNo=XXXXX
     AP 11개 브랜드 전 제품 수집 → dim_product

  B) 주간 베스트셀러 순위 (fetch_and_store) — 매주
     URL: /display/page/best-seller
     AP 브랜드 필터링 → 실제 순위 번호 보존 → fact_retail_rankings

VOC 수집은 tools/collect/voc_collector.py 에서 분리 관리.
분석 단계에서 트렌드 확정 제품에 대해서만 on-demand 수집.
"""

import re
import asyncio
import sqlite3
import logging
from datetime import datetime, timezone
from typing import Union

from playwright.async_api import async_playwright
from tools.collect._ap_brands import is_ap_brand, ap_brand_id, OY_AP_BRAND_PAGES
from tools.collect._product_utils import next_product_id, find_matching_product

logger = logging.getLogger(__name__)

BASE_URL     = "https://global.oliveyoung.com"
BEST_URL     = "/display/page/best-seller"
PRODUCT_URL  = "/product/detail?prdtNo="
MAX_REVIEWS  = 50
SCROLL_DELAY = 2

# 수집할 카테고리 → (카테고리명, 버튼 텍스트)
CATEGORIES = [
    ("skincare", "Skincare"),
    ("suncare",  "Suncare"),
    ("makeup",   "Makeup"),
    ("face masks", "Face Masks")
]


def _guess_brand_from_text(text: str) -> str:
    """
    타일에서 브랜드 필드가 비어있을 때 제품명/텍스트에서 AP 브랜드명을 추정.
    """
    bid = ap_brand_id(text or "")
    return bid or ""


async def _scrape_product_detail(page, prdtno: str) -> dict:
    """
    제품 상세 페이지에서 실제 카테고리 + 성분/효능 수집.

    카테고리: <span class="loc_cat"> 브레드크럼
      예) Home > Skincare > Toner
          → category_main = 'skincare'
          → category_sub  = 'toner'

    성분/효능: Vue.js CSR → domcontentloaded + loc_cat 대기 + 아코디언 클릭
      key_benefits:    [data-testid="product-whyweloveit-content"]
      key_ingredients: [data-testid="product-featuredingredients-content"]
    """
    url = f"{BASE_URL}{PRODUCT_URL}{prdtno}"
    result = {
        "category_main":   None,
        "category_sub":    None,
        "key_ingredients": None,
        "key_benefits":    None,
    }
    try:
        # domcontentloaded 후 Vue.js 렌더링 대기 (networkidle은 SPA에서 안 끝남)
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        try:
            await page.wait_for_selector("span.loc_cat", timeout=8_000)
        except Exception:
            pass
        await page.wait_for_timeout(2000)

        # ── 카테고리: 브레드크럼 ──
        cats = await page.query_selector_all("span.loc_cat")
        cat_texts = []
        for c in cats:
            t = (await c.inner_text()).strip()
            if t and t.lower() not in ("home", ""):
                cat_texts.append(t)

        if cat_texts:
            result["category_main"] = cat_texts[0].lower()
            result["category_sub"]  = cat_texts[-1].lower() if len(cat_texts) > 1 else None

        # ── 아코디언 펼치기 (aria-expanded="false" 인 경우 클릭) ──
        for link_testid in [
            "product-whyweloveit-link",
            "product-featuredingredients-link",
        ]:
            try:
                link = await page.query_selector(f'[data-testid="{link_testid}"]')
                if link:
                    expanded = await link.get_attribute("aria-expanded")
                    if expanded == "false":
                        await link.click()
                        await page.wait_for_timeout(800)
            except Exception:
                pass

        # ── key_benefits: Why We Love It ──
        benefits_el = await page.query_selector('[data-testid="product-whyweloveit-content"]')
        if benefits_el:
            text = (await benefits_el.inner_text()).strip()
            if text:
                result["key_benefits"] = text

        # ── key_ingredients: Featured Ingredients ──
        ingr_el = await page.query_selector('[data-testid="product-featuredingredients-content"]')
        if ingr_el:
            text = (await ingr_el.inner_text()).strip()
            if text:
                result["key_ingredients"] = text

    except Exception as e:
        logger.debug(f"제품 상세 수집 실패 ({prdtno}): {e}")

    return result


def _clean_product_name(raw: str, brand_name: str = "") -> str:
    """
    OY Global 제품명 정제.

    제거 대상:
      0. ★...★ 프로모션 라벨 (★2025 Awards★, ★$9.99 DEAL★)
      1. [TOY STORY EDITION] 같은 기획전/콜라보 라벨
      2. 앞에 붙은 브랜드명 (brand_id에 이미 존재)
      3. (RENEWAL), (NEW), (LIMITED) 등 버전 표시
      4. (+Keyring), (+Gift) 등 증정품 표시
      5. Double Pack, 2EA, Trial Kit, Special Set 등 묶음 표시 (말미)
      6. N+N 번들 표시 (1+1, 2+1 등)
      7. (N Shades), (N Options), (N Colors) 등 배리언트 수량 표시

    유지 대상:
      - 용량 (50ml, 80mL 등) — 제품 구분에 필요
      - SPF/PA 표시 — 선크림 구분에 필요
      - Mini, Travel 등 사이즈 변형 표시
    """
    name = raw.strip()

    # 0. ★...★ 프로모션 라벨 제거: ★2025 Awards★, ★$9.99 DEAL★ 등
    name = re.sub(r'★[^★]*★\s*', '', name).strip()

    # 1. 대괄호 라벨 제거: [TOY STORY EDITION], [LIMITED], [GIFT SET] 등
    name = re.sub(r'^\s*\[[^\]]*\]\s*', '', name)

    # 2. 앞에 붙은 브랜드명 제거 (대소문자 무시)
    if brand_name:
        name = re.sub(rf'^\s*{re.escape(brand_name)}\s+', '', name, flags=re.IGNORECASE)

    # 3. 버전/상태 괄호 제거: (RENEWAL), (NEW), (LIMITED EDITION), (REFILL)
    name = re.sub(r'\s*\((RENEWAL|NEW|REFILL|LIMITED|LIMITED EDITION|RELAUNCH|UPGRADE)\)', '', name, flags=re.IGNORECASE)

    # 4. 증정품 괄호 제거: (+Keyring), (+Pouch), (+Sample)
    name = re.sub(r'\s*\(\+[^)]+\)', '', name)

    # 5. 말미 묶음/세트 표시 제거
    #    - 기존: Double Pack, Trial Kit, Starter Kit, 2EA, Value Set
    #    - 추가: Special Set, double set, N ea Set
    name = re.sub(
        r'\s+(Double Pack|Trial Kit|Starter Kit|\d+EA|Value Set|Special Set|double set|\d+\s*ea\s*Set)$',
        '', name, flags=re.IGNORECASE
    )

    # 6. N+N 번들 표시 제거: 1+1, 2+1 등 (말미 Set/Special Set 포함)
    name = re.sub(r'\s+\d\+\d(\s+(Special\s+)?Set)?$', '', name, flags=re.IGNORECASE).strip()

    # 7. (N Shades), (N Options), (N Colors), (N Types) 배리언트 수량 제거
    name = re.sub(r'\s*\(\d+\s+(Options?|Shades?|Colors?|Types?)\)', '', name, flags=re.IGNORECASE)

    # 8. 공백 정리
    name = re.sub(r'\s{2,}', ' ', name).strip()

    return name if name else raw.strip()


def _parse_date(date_str: str) -> Union[str, None]:
    if not date_str:
        return None
    from datetime import datetime as dt
    for fmt in ["%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d", "%b %d, %Y", "%B %d, %Y"]:
        try:
            return dt.strptime(date_str.strip(), fmt).strftime("%Y/%m/%d")
        except ValueError:
            pass
    return date_str.strip()


class OYGlobalTool:
    PLATFORM_ID = "oy_global"

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
        """product_name(소문자) + prdtNo → product_id 조회 맵"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT product_id, product_name_en, oy_prdtno FROM dim_product"
            ).fetchall()
        result = {}
        for r in rows:
            result[r["product_name_en"].lower()] = r["product_id"]
            if r["oy_prdtno"]:
                result[f"prdtno:{r['oy_prdtno']}"] = r["product_id"]
        return result

    def _build_catalog_prdtno_set(self) -> dict[str, str]:
        """카탈로그에 등록된 prdtNo → brand_id 맵 (AP 여부 판별용)"""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT oy_prdtno, brand_id FROM dim_product WHERE oy_prdtno IS NOT NULL"
            ).fetchall()
        return {r["oy_prdtno"]: r["brand_id"] for r in rows}

    def _match_product_id(self, name: str, product_map: dict) -> str | None:
        n = name.lower().strip()
        if n in product_map:
            return product_map[n]
        for key, pid in product_map.items():
            if key.startswith("prdtno:"):
                continue
            if len(key) > 4 and (key in n or n in key):
                return pid
        return None

    def _make_brand_id(self, brand: str) -> str:
        """브랜드명 → dim_brand.brand_id (AP 계열 전용)"""
        return ap_brand_id(brand) or re.sub(r'[^a-z0-9]', '_', brand.lower().strip()).strip('_')

    # ──────────────────────────────────────────
    # A. 랭킹 스크래핑
    # ──────────────────────────────────────────
    async def _close_popups(self, page):
        for sel in ["button.btn-close", ".top-banner-close-btn", "#confirm-btn",
                    "button:has-text('Close')", "button:has-text('확인')"]:
            try:
                el = page.locator(sel)
                if await el.count() > 0 and await el.first.is_visible():
                    await el.first.click(timeout=2000)
                    await page.wait_for_timeout(500)
            except Exception:
                pass

    async def _scrape_category(self, page, category: str, btn_text: str) -> list[dict]:
        """
        Best Seller 페이지 카테고리 탭 클릭 → 상품 타일 수집.

        확인된 HTML 구조:
          탭:     <a class="btn bestCategory">Skincare</a>
          타일:   <li class="order-best-product prdt-unit">
          prdtNo: <input type="hidden" name="prdtNo" value="GA...">
          제품명: <input type="hidden" name="prdtName" value="...">
          순위:   <div class="rank-badge"><span>1</span></div>
        """
        results = []
        try:
            # ── 카테고리 탭 클릭: a.btn.bestCategory ──
            tab = page.locator(f"a.btn.bestCategory").filter(has_text=btn_text)
            if await tab.count() > 0:
                await tab.first.click()
                # 탭 전환 후 상품 타일 갱신 대기
                try:
                    await page.wait_for_selector(
                        "li.order-best-product", timeout=8_000
                    )
                except Exception:
                    pass
                await page.wait_for_timeout(1500)
            else:
                logger.warning(f"  OY Global [{category}]: 탭 '{btn_text}' 못 찾음")

            # ── 상품 타일 수집: li.order-best-product ──
            tiles = await page.query_selector_all("li.order-best-product")
            logger.info(f"  OY Global [{category}]: 상품 타일 {len(tiles)}개 발견")

            seen_prdtno = set()
            for tile in tiles:
                try:
                    # prdtNo: hidden input
                    prdtno_el = await tile.query_selector('input[name="prdtNo"]')
                    prdtNo = (await prdtno_el.get_attribute("value") or "").strip() if prdtno_el else ""
                    if not prdtNo or prdtNo in seen_prdtno:
                        continue
                    seen_prdtno.add(prdtNo)

                    # 제품명: hidden input prdtName (영문명 바로 사용)
                    name_el = await tile.query_selector('input[name="prdtName"]')
                    raw_name = (await name_el.get_attribute("value") or "").strip() if name_el else ""
                    if not raw_name:
                        img = await tile.query_selector("img")
                        raw_name = (await img.get_attribute("alt") or "").strip() if img else ""
                    if not raw_name:
                        continue
                    brand_guess = _guess_brand_from_text(raw_name)
                    product_name = _clean_product_name(raw_name, brand_guess)

                    # 순위: div.rank-badge span
                    rank = 0
                    rank_el = await tile.query_selector("div.rank-badge span")
                    if rank_el:
                        try:
                            rank = int((await rank_el.inner_text()).strip())
                        except ValueError:
                            pass

                    # URL
                    link_el = await tile.query_selector("a[href*='prdtNo']")
                    href = (await link_el.get_attribute("href") or "") if link_el else ""
                    full_url = BASE_URL + href if href.startswith("/") else href

                    results.append({
                        "rank":         rank,
                        "product_name": product_name,
                        "brand":        brand_guess,  # 카탈로그 비어있을 때 보조 판별
                        "category":     category,
                        "url":          full_url,
                        "prdtNo":       prdtNo,
                    })

                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"OY Global [{category}] 수집 오류: {e}")

        logger.info(f"  OY Global [{category}]: {len(results)}개 수집")
        return results

    async def _scrape_rankings(self, page) -> dict[str, list[dict]]:
        """
        Best Seller 페이지 접속 → 카테고리별 순위 수집.
        반환: {category: [제품 리스트]}
        """
        url = BASE_URL + BEST_URL
        await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
        await page.wait_for_timeout(3000)
        await self._close_popups(page)

        await page.evaluate("window.scrollBy(0, 600)")
        await page.wait_for_timeout(1500)
        try:
            await page.wait_for_selector("a[href*='prdtNo']", timeout=25_000)
        except Exception:
            logger.warning("OY Global: 제품 링크 로딩 타임아웃")

        all_results = {}
        for category, btn_text in CATEGORIES:
            results = await self._scrape_category(page, category, btn_text)
            all_results[category] = results
            await page.wait_for_timeout(1500)

        return all_results

    async def _scrape_top_in_korea(self, page) -> list[dict]:
        """
        Best Seller 페이지 내 'Top in Korea' 섹션 수집.
        반환: [제품 리스트] (rank, product_name, prdtNo 포함)
        """
        url = BASE_URL + BEST_URL
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            await page.wait_for_timeout(3000)
            await self._close_popups(page)
            await page.evaluate("window.scrollBy(0, 600)")
            await page.wait_for_timeout(1500)
        except Exception as e:
            logger.warning(f"OY Top in Korea 페이지 접속 실패: {e}")
            return []

        # "Top in Korea" 또는 "Best in Korea" 탭/섹션 탐색
        results = []
        for btn_text in ["Top in Korea", "Best in Korea", "Korea Best"]:
            try:
                tab = page.locator("a.btn, button.btn, a.tab-btn, button").filter(has_text=btn_text)
                if await tab.count() > 0:
                    await tab.first.click()
                    await page.wait_for_timeout(2000)
                    logger.info(f"OY Top in Korea: '{btn_text}' 탭 클릭 성공")
                    break
            except Exception:
                continue

        # 상품 타일 수집 (Best Seller 페이지와 동일한 구조)
        tiles = await page.query_selector_all("li.order-best-product")
        seen = set()
        for tile in tiles:
            try:
                prdtno_el = await tile.query_selector('input[name="prdtNo"]')
                prdtNo = (await prdtno_el.get_attribute("value") or "").strip() if prdtno_el else ""
                if not prdtNo or prdtNo in seen:
                    continue
                seen.add(prdtNo)

                name_el = await tile.query_selector('input[name="prdtName"]')
                raw_name = (await name_el.get_attribute("value") or "").strip() if name_el else ""
                if not raw_name:
                    continue

                brand_guess = _guess_brand_from_text(raw_name)
                product_name = _clean_product_name(raw_name, brand_guess)

                rank = 0
                rank_el = await tile.query_selector("div.rank-badge span")
                if rank_el:
                    try:
                        rank = int((await rank_el.inner_text()).strip())
                    except ValueError:
                        pass

                link_el = await tile.query_selector("a[href*='prdtNo']")
                href = (await link_el.get_attribute("href") or "") if link_el else ""
                full_url = BASE_URL + href if href.startswith("/") else href

                results.append({
                    "rank":         rank,
                    "product_name": product_name,
                    "brand":        brand_guess,
                    "category":     "skincare",
                    "url":          full_url,
                    "prdtNo":       prdtNo,
                })
            except Exception:
                continue

        logger.info(f"OY Top in Korea: {len(results)}개 수집")
        return results

    # ──────────────────────────────────────────
    # B. VOC 수집 (검증된 셀렉터)
    # ──────────────────────────────────────────
    async def _collect_reviews(self, page, prdtNo: str) -> list[dict]:
        url = BASE_URL + PRODUCT_URL + prdtNo
        reviews = []
        seen_texts = set()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            await page.wait_for_timeout(4000)
            await self._close_popups(page)

            # 리뷰 섹션까지 스크롤
            for rev_sel in ['.prd-review-list', '.review-list', '[class*="review"]', '#reviewArea']:
                try:
                    el = await page.query_selector(rev_sel)
                    if el:
                        await el.scroll_into_view_if_needed()
                        await page.wait_for_timeout(2000)
                        break
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"VOC 페이지 접속 실패 ({prdtNo}): {e}")
            return []

        no_new_count = 0

        for _ in range(30):
            if len(reviews) >= MAX_REVIEWS:
                break

            items = []
            for sel in ['.prd-review-list > li', '.review-list > li',
                        '.product-review-unit-main', '.review-item',
                        '[class*="review-unit"]', '[class*="review-item"]',
                        'li[class*="review"]']:
                found = await page.query_selector_all(sel)
                if found:
                    items = found
                    break

            if not items:
                prev_h = await page.evaluate("document.body.scrollHeight")
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(SCROLL_DELAY * 1000)
                new_h = await page.evaluate("document.body.scrollHeight")
                if new_h <= prev_h:
                    break
                continue

            prev_count = len(reviews)

            for item in items:
                if len(reviews) >= MAX_REVIEWS:
                    break
                try:
                    # 텍스트
                    text = None
                    for t_sel in ['.review-unit-cont-comment', '.review-unit-cont',
                                  '.review-text', '[class*="comment"]', 'p']:
                        el = await item.query_selector(t_sel)
                        if el:
                            t = (await el.inner_text()).strip()
                            if t and len(t) > 10:
                                text = t
                                break
                    if not text:
                        text = (await item.inner_text()).strip()
                    if not text or len(text) < 10:
                        continue
                    text = re.sub(r'\s*Translate\s*$', '', text, flags=re.IGNORECASE).strip()
                    if text in seen_texts:
                        continue
                    seen_texts.add(text)

                    # 별점
                    rating = 0.0
                    try:
                        container = await item.query_selector('.review-star-rating') or item
                        star_wraps = await container.query_selector_all('.wrap-icon-star')
                        if star_wraps:
                            filled = sum(
                                1.0 if len(await sw.query_selector_all('.icon-star.filled')) >= 2
                                else 0.5 if len(await sw.query_selector_all('.icon-star.filled')) == 1
                                else 0
                                for sw in star_wraps
                            )
                            rating = filled
                    except Exception:
                        pass

                    # 날짜
                    date_str = ""
                    for d_sel in ['.review-info .date', '.review-write-info-date', '.date', 'time']:
                        el = await item.query_selector(d_sel)
                        if el:
                            date_str = (await el.inner_text()).strip()
                            if date_str:
                                break

                    # Helpful
                    helpful = 0
                    for h_sel in ['.btn-helpful .count', '.helpful-count']:
                        el = await item.query_selector(h_sel)
                        if el:
                            digits = ''.join(filter(str.isdigit, await el.inner_text()))
                            helpful = int(digits) if digits else 0
                            if helpful:
                                break

                    reviews.append({
                        "title":           None,
                        "rating":          float(rating) if rating else None,
                        "text":            text,
                        "helpful":         helpful,
                        "submission_time": _parse_date(date_str),
                    })
                except Exception:
                    continue

            if len(reviews) == prev_count:
                no_new_count += 1
                if no_new_count >= 3:
                    break
            else:
                no_new_count = 0

            await page.evaluate("window.scrollBy(0, 500)")
            await page.wait_for_timeout(1000)
            for more_sel in ['button.review-list-more-btn', "button:has-text('More')", '.btn-more']:
                try:
                    btn = page.locator(more_sel)
                    if await btn.count() > 0 and await btn.first.is_visible():
                        await btn.first.scroll_into_view_if_needed()
                        await btn.first.click(force=True)
                        await page.wait_for_timeout(SCROLL_DELAY * 1000)
                        break
                except Exception:
                    pass

        logger.info(f"  VOC [{prdtNo}]: {len(reviews)}개")
        return reviews

    # ──────────────────────────────────────────
    # A. 브랜드 페이지 카탈로그 수집
    # ──────────────────────────────────────────
    async def _scrape_brand_catalog(self, page, brand_id: str, brand_name: str, brand_no: str) -> list[dict]:
        """
        AP 브랜드 페이지에서 전체 제품 목록 수집.
        URL: /display/page/brand-page?brandNo=XXXXX

        Phase 1: 브랜드 페이지 스크롤 → 모든 prdtNo 링크 수집
        Phase 2: 각 제품 상세 페이지 방문 → <span class="loc_cat"> 카테고리 추출
        """
        url = f"{BASE_URL}/display/page/brand-page?brandNo={brand_no}"
        raw_products: list[dict] = []
        seen: set[str] = set()

        # ── Phase 1: 브랜드 페이지에서 전체 제품 링크 수집 ──
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            await page.wait_for_timeout(3000)
            await self._close_popups(page)

            # More 버튼이 사라질 때까지 반복 클릭 (최대 20회)
            for _ in range(20):
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(1000)

                more_btn = page.locator("button.product-list-more-btn")
                if await more_btn.count() == 0 or not await more_btn.first.is_visible():
                    break  # More 버튼 없음 → 전체 로드 완료

                await more_btn.first.scroll_into_view_if_needed()
                prev_count = await page.evaluate(
                    "document.querySelectorAll('div.product-list div.product-unit').length"
                )
                await more_btn.first.click()

                # 새 제품이 실제로 추가될 때까지 대기 (최대 5초)
                for _ in range(10):
                    await page.wait_for_timeout(500)
                    new_count = await page.evaluate(
                        "document.querySelectorAll('div.product-list div.product-unit').length"
                    )
                    if new_count > prev_count:
                        break

            # 링크도 product-list 안에서만 수집 (swiper 캐러셀 제외)
            links = await page.query_selector_all("div.product-list a[href*='prdtNo']")
            for link in links:
                try:
                    href = await link.get_attribute("href") or ""
                    m = re.search(r'prdtNo=([A-Z0-9]+)', href)
                    if not m:
                        continue
                    prdtno = m.group(1)
                    if prdtno in seen:
                        continue
                    seen.add(prdtno)

                    img = await link.query_selector("img")
                    raw_name = (await img.get_attribute("alt") or "").strip() if img else ""
                    if not raw_name:
                        raw_name = (await link.inner_text()).strip()[:100]
                    if not raw_name:
                        continue

                    product_name = _clean_product_name(raw_name, brand_name)
                    full_url = BASE_URL + href if href.startswith("/") else href
                    raw_products.append({
                        "prdtno":       prdtno,
                        "product_name": product_name,
                        "url":          full_url,
                        "brand_id":     brand_id,
                    })
                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"카탈로그 수집 실패 [{brand_name}]: {e}")
            return []

        logger.info(f"  카탈로그 [{brand_name}]: {len(raw_products)}개 링크 수집 완료")
        # Phase 2 (상세 페이지)는 _build_catalog_async에서 전역 Semaphore로 병렬 처리
        return raw_products  # (prdtno, product_name, url, brand_id) 리스트 반환

    async def _build_catalog_async(self) -> dict:
        """
        AP 11개 브랜드 전 제품 → dim_product 카탈로그 구축.

        Phase 1: 브랜드 페이지에서 prdtNo 링크 수집 (Semaphore(3), 11개 브랜드 병렬)
        Phase 2: 전체 prdtNo 상세 페이지 방문 (Semaphore(8), 전역 병렬)
                 → networkidle + 아코디언 클릭 → category/benefits/ingredients 추출
        캐싱: 이미 category_main이 있는 제품은 상세 페이지 방문 스킵
        """
        UA = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )

            # ── Phase 1: 브랜드 페이지 링크 수집 (Semaphore 3) ──
            brand_sem = asyncio.Semaphore(3)

            async def collect_links(brand_id, brand_name, brand_no):
                async with brand_sem:
                    ctx = await browser.new_context(
                        viewport={"width": 1280, "height": 900},
                        user_agent=UA,
                    )
                    page = await ctx.new_page()
                    try:
                        raw = await self._scrape_brand_catalog(
                            page, brand_id, brand_name, brand_no
                        )
                    finally:
                        await ctx.close()
                return raw

            brand_tasks = [
                collect_links(bid, bname, bno)
                for bid, bname, bno in OY_AP_BRAND_PAGES
            ]
            brand_results = await asyncio.gather(*brand_tasks, return_exceptions=True)

            # 전체 raw_products 취합
            all_raw: list[dict] = []
            for r in brand_results:
                if isinstance(r, list):
                    all_raw.extend(r)
                else:
                    logger.warning(f"브랜드 링크 수집 실패: {r}")

            logger.info(f"Phase 1 완료: 총 {len(all_raw)}개 제품 링크 수집")

            # ── 캐싱: 이미 category_main 있는 제품 스킵 ──
            with self._conn() as conn:
                cached = {
                    row[0] for row in conn.execute(
                        "SELECT oy_prdtno FROM dim_product WHERE category_main IS NOT NULL"
                    ).fetchall()
                }
            to_visit = [rp for rp in all_raw if rp["prdtno"] not in cached]
            skip_count = len(all_raw) - len(to_visit)
            if skip_count:
                logger.info(f"  캐시 스킵: {skip_count}개 (이미 카테고리 있음)")
            logger.info(f"  상세 페이지 방문 대상: {len(to_visit)}개")

            # ── Phase 2: Page Pool — 고정 창 N개만 생성 후 재사용 ──
            POOL_SIZE = 5  # 동시 열려있는 창 수 (고정)
            pool_ctxs  = []
            pool_pages = []
            for _ in range(POOL_SIZE):
                ctx  = await browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    user_agent=UA,
                )
                page = await ctx.new_page()
                pool_ctxs.append(ctx)
                pool_pages.append(page)

            # asyncio.Queue로 페이지 풀 관리
            page_queue: asyncio.Queue = asyncio.Queue()
            for p in pool_pages:
                await page_queue.put(p)

            async def fetch_detail(rp: dict) -> dict:
                page = await page_queue.get()   # 빈 창 가져오기
                try:
                    detail = await _scrape_product_detail(page, rp["prdtno"])

                    # category_main 없으면 1회 재시도 (타임아웃/rate-limit 대응)
                    if not detail.get("category_main"):
                        logger.debug(f"  재시도: {rp['prdtno']}")
                        await asyncio.sleep(1.5)
                        detail = await _scrape_product_detail(page, rp["prdtno"])

                except Exception as e:
                    logger.debug(f"fetch_detail 오류 ({rp['prdtno']}): {e}")
                    detail = {"category_main": None, "category_sub": None,
                              "key_ingredients": None, "key_benefits": None}
                finally:
                    await page_queue.put(page)  # 창 반납

                return {
                    "brand_id":        rp["brand_id"],
                    "product_name_en": rp["product_name"],
                    "category_main":   detail["category_main"],
                    "category_sub":    detail["category_sub"],
                    "key_ingredients": detail["key_ingredients"],
                    "key_benefits":    detail["key_benefits"],
                    "oy_url":          rp["url"],
                    "oy_prdtno":       rp["prdtno"],
                }

            detail_tasks = [fetch_detail(rp) for rp in to_visit]
            detail_results = await asyncio.gather(*detail_tasks, return_exceptions=True)

            # 풀 정리
            for ctx in pool_ctxs:
                await ctx.close()

            await browser.close()

        all_products = [r for r in detail_results if isinstance(r, dict)]
        logger.info(f"Phase 2 완료: {len(all_products)}개 상세 수집")

        # ── DB 저장 ──
        inserted = updated = 0
        with self._conn() as conn:
            for p in all_products:
                try:
                    prdtno = p.get("oy_prdtno", "")

                    # 이미 등록된 제품인지 확인 (oy_prdtno 기준)
                    existing = None
                    if prdtno:
                        row = conn.execute(
                            "SELECT product_id FROM dim_product WHERE oy_prdtno = ?",
                            (prdtno,)
                        ).fetchone()
                        existing = row["product_id"] if row else None

                    if existing:
                        # 기존 제품 업데이트 (카테고리/재료 정보 보완)
                        conn.execute("""
                            UPDATE dim_product SET
                                product_name_en = :product_name_en,
                                category_main   = COALESCE(:category_main, category_main),
                                category_sub    = COALESCE(:category_sub, category_sub),
                                key_ingredients = COALESCE(:key_ingredients, key_ingredients),
                                key_benefits    = COALESCE(:key_benefits, key_benefits),
                                oy_url          = COALESCE(:oy_url, oy_url),
                                updated_at      = datetime('now')
                            WHERE product_id = :pid
                        """, {**p, "pid": existing})
                        updated += 1
                    else:
                        # 신규 제품 — 서로게이트 키 채번
                        pid = next_product_id(conn)
                        conn.execute("""
                            INSERT OR IGNORE INTO dim_product
                                (product_id, brand_id, product_name_en,
                                 category_main, category_sub,
                                 key_ingredients, key_benefits,
                                 oy_prdtno, oy_url)
                            VALUES (:product_id, :brand_id, :product_name_en,
                                    :category_main, :category_sub,
                                    :key_ingredients, :key_benefits,
                                    :oy_prdtno, :oy_url)
                        """, {**p, "product_id": pid})
                        inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception as e:
                    logger.debug(f"카탈로그 등록 실패: {e}")

        total = len(all_raw)
        logger.info(f"카탈로그 구축 완료: 전체 {total}개 / 신규+업데이트 {inserted}개 / 스킵 {skip_count}개")
        return {"total": total, "inserted": inserted, "skipped": skip_count}

    def build_catalog(self) -> dict:
        """AP 전체 제품 카탈로그 구축 (일회성 or 월 1회 실행)."""
        logger.info("OY Global 카탈로그 구축 시작")
        return asyncio.run(self._build_catalog_async())

    # ──────────────────────────────────────────
    # 주간 랭킹 수집
    # ──────────────────────────────────────────
    async def _run_async(self, week: str) -> dict:
        product_map    = self._build_product_map()
        catalog_prdtno = self._build_catalog_prdtno_set()  # prdtNo → brand_id (AP 판별)
        ranking_rows: list[tuple] = []
        new_products: list[dict]  = []

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )
            ctx = await browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = await ctx.new_page()

            # ① 랭킹 수집 (Top Orders — 카테고리별)
            all_rankings = await self._scrape_rankings(page)

            # ② Top in Korea 수집
            korea_rankings = await self._scrape_top_in_korea(page)

            # AP 제품 판별: prdtNo → 카탈로그 매칭 우선, 브랜드명 감지 보조
            voc_targets: list[tuple[str, str, str]] = []

            # Top Orders + Top in Korea 통합 처리
            # all_rankings: {category: [items]} → (platform_id, items) 목록으로 변환
            combined_sources: list[tuple[str, str, list[dict]]] = []
            for category, items in all_rankings.items():
                combined_sources.append(("oy_top_orders", category, items))
            if korea_rankings:
                combined_sources.append(("oy_top_korea", "skincare", korea_rankings))

            total_seen = 0
            for platform_src, category, rankings in combined_sources:
                for item in rankings:
                    prdtno = item.get("prdtNo", "")
                    brand  = item.get("brand", "")
                    total_seen += 1

                    # AP 판별: ① 카탈로그 prdtNo 매칭 → ② 브랜드명 감지
                    brand_id = None
                    if prdtno and prdtno in catalog_prdtno:
                        brand_id = catalog_prdtno[prdtno]
                    elif is_ap_brand(brand):
                        brand_id = self._make_brand_id(brand)
                    elif brand:
                        brand_id = brand if "_" in brand or brand.islower() else self._make_brand_id(brand)

                    if not brand_id:
                        continue  # AP 아님

                    pid = product_map.get(f"prdtno:{prdtno}") \
                          or self._match_product_id(item["product_name"], product_map)
                    if not pid:
                        new_products.append({
                            "brand_id":        brand_id,
                            "product_name_en": item["product_name"],
                            "category_main":   None,
                            "category_sub":    None,
                            "key_ingredients": None,
                            "key_benefits":    None,
                            "oy_url":          item.get("url", ""),
                            "oy_prdtno":       prdtno,
                        })
                        pid = f"__new__{prdtno or item['product_name'][:20]}"
                        product_map[item["product_name"].lower()] = pid
                        if prdtno:
                            product_map[f"prdtno:{prdtno}"] = pid

                    ranking_rows.append((week, platform_src, pid, item["rank"], category))

            logger.info(
                f"  랭킹 수집: 전체 {total_seen}개 중 AP {len(ranking_rows)}개 "
                f"(Top Orders + Top in Korea 포함, 카탈로그 {len(catalog_prdtno)}개 등록)"
            )

            # 신규 제품 카테고리: 상세 페이지 방문으로 채움
            if new_products:
                logger.info(f"  신규 제품 {len(new_products)}개 → 상세 페이지 카테고리 수집")
                for p in new_products:
                    prdtno = p.get("oy_prdtno", "")
                    if prdtno:
                        detail = await _scrape_product_detail(page, prdtno)
                        p["category_main"]   = detail["category_main"]
                        p["category_sub"]    = detail["category_sub"]
                        p["key_ingredients"] = detail["key_ingredients"]
                        p["key_benefits"]    = detail["key_benefits"]
                    await page.wait_for_timeout(500)

            await browser.close()

        # DB 저장
        new_product_count = ranking_count = 0
        tmp_pid_to_real: dict[str, str] = {}
        with self._conn() as conn:
            for p in new_products:
                try:
                    # 서로게이트 키 채번
                    pid = next_product_id(conn)
                    conn.execute("""
                        INSERT OR IGNORE INTO dim_product
                            (product_id, brand_id, product_name_en,
                             category_main, category_sub,
                             oy_prdtno, oy_url)
                        VALUES (:product_id, :brand_id, :product_name_en,
                                :category_main, :category_sub,
                                :oy_prdtno, :oy_url)
                    """, {**p, "product_id": pid})
                    changes = conn.execute("SELECT changes()").fetchone()[0]
                    new_product_count += changes
                    if changes:
                        tmp_key = f"__new__{p.get('oy_prdtno') or p['product_name_en'][:20]}"
                        tmp_pid_to_real[tmp_key] = pid
                        # product_map 업데이트 (이후 ranking_rows 재매핑용)
                        product_map[p["product_name_en"].lower()] = pid
                        if p.get("oy_prdtno"):
                            product_map[f"prdtno:{p['oy_prdtno']}"] = pid
                except Exception as e:
                    logger.debug(f"dim_product 등록 실패: {e}")
            for row in ranking_rows:
                pid = row[2]
                if str(pid).startswith("__new__"):
                    pid = tmp_pid_to_real.get(str(pid))
                    if not pid:
                        continue
                try:
                    conn.execute("""
                        INSERT OR REPLACE INTO fact_retail_rankings
                            (week, platform_id, product_id, rank_position, category)
                        VALUES (?, ?, ?, ?, ?)
                    """, (row[0], row[1], pid, row[3], row[4]))
                    ranking_count += conn.execute("SELECT changes()").fetchone()[0]
                except Exception:
                    pass

        logger.info(
            f"OY Global 완료: 랭킹 {ranking_count}건, 신규 제품 {new_product_count}건"
        )
        return {"rankings": ranking_count, "new_products": new_product_count}

    def fetch_and_store(self, week: str = None) -> dict:
        week = week or self._current_week()
        logger.info(f"OY Global 수집 시작 (week={week})")
        return asyncio.run(self._run_async(week))