"""
voc_collector.py
──────────────────
트렌드 확정 제품 전용 VOC(리뷰) 수집기.

분석 단계에서 트렌드로 판별된 product_id 목록을 받아
해당 제품의 리뷰만 on-demand로 수집 → fact_raw_reviews 저장.

사용처:
  분석 에이전트가 트렌드 제품 선별 후 호출
  (수집 단계에서는 호출하지 않음)

지원 플랫폼:
  - sephora_us : BazaarVoice REST API (HTTP, 빠름)
  - oy_global  : Playwright 헤드리스 브라우저 스크래핑

인터페이스:
  collector = VocCollector(db_path)
  collector.collect(products, week)

  products 형식:
    [
      {"product_id": "S00000001", "platform": "sephora_us", "platform_pid": "P482692"},
      {"product_id": "S00000002", "platform": "oy_global",  "platform_pid": "A000123456"},
      ...
    ]
"""

import asyncio
import logging
import re
import sqlite3
import time
from datetime import datetime
from typing import Union

import requests
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

# ── 상수 ────────────────────────────────────────────────
OY_BASE_URL    = "https://global.oliveyoung.com"
OY_PRODUCT_URL = "/product/detail?prdtNo="
MAX_RECENT     = 10   # 최신순 수집
MAX_HELPFUL    = 10   # 도움됨순 수집
MAX_COLLECT    = 30   # OY 스크랩 풀 크기 (로컬에서 20개 선별)
SCROLL_DELAY   = 2
VOC_MAX_RETRY   = 2   # 일시적 실패 대비 제품별 재시도 횟수
VOC_RETRY_DELAY = 2   # 재시도 간 대기 (초)

BV_REVIEWS_URL = "https://api.bazaarvoice.com/data/reviews.json"
BV_STATS_URL   = "https://api.bazaarvoice.com/data/statistics.json"
BV_PASSKEY     = "calXm2DyQVjcCy9agq85vmTJv5ELuuBCF2sdg4BnJzJus"
BV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.sephora.com/",
}


def _parse_date(date_str: str) -> Union[str, None]:
    if not date_str:
        return None
    for fmt in ["%Y/%m/%d", "%Y-%m-%d", "%Y.%m.%d", "%b %d, %Y", "%B %d, %Y"]:
        try:
            return datetime.strptime(date_str.strip(), fmt).strftime("%Y/%m/%d")
        except ValueError:
            pass
    return date_str.strip()


class VocCollector:
    """트렌드 확정 제품 전용 VOC 수집기."""

    def __init__(self, db_path: str, headless: bool = True):
        self.db_path  = db_path
        self.headless = headless
        # 플랫폼 전체 통계: {(platform, product_id): {"total_reviews": int, "platform_avg_rating": float}}
        self.platform_stats: dict = {}
        # OY 임시 통계: {prdtno: (total_reviews, platform_avg_rating)}
        self._oy_stats: dict = {}

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ──────────────────────────────────────────
    # 공개 인터페이스
    # ──────────────────────────────────────────
    def collect(self, products: list[dict], week: str) -> dict:
        """
        트렌드 제품 목록의 리뷰를 수집해 fact_raw_reviews에 저장.

        Args:
            products: [{"product_id", "platform", "platform_pid"}, ...]
            week:     'YYYY-WNN'

        Returns:
            {"sephora_us": N, "oy_global": M, "total": N+M}
        """
        sep_products = [p for p in products if p["platform"] == "sephora_us"]
        oy_products  = [p for p in products if p["platform"] == "oy_global"]

        sep_count = self._collect_sephora(sep_products, week) if sep_products else 0
        oy_count  = self._collect_oy_global(oy_products, week) if oy_products else 0

        total = sep_count + oy_count
        logger.info(f"VOC 수집 완료: Sephora {sep_count}건 / OY Global {oy_count}건")
        return {"sephora_us": sep_count, "oy_global": oy_count, "total": total}

    # ──────────────────────────────────────────
    # Sephora — BazaarVoice REST API
    # ──────────────────────────────────────────
    def _collect_sephora(self, products: list[dict], week: str) -> int:
        """BazaarVoice API로 Sephora 리뷰 수집 (최신 10 + 도움됨 10) + 전체 통계."""
        total = 0
        for p in products:
            reviews = self._fetch_bv_reviews_mixed(p["platform_pid"])
            total  += self._save_reviews(reviews, week, "sephora_us", p["product_id"])
            # 플랫폼 전체 통계 캡처 (velocity·별점 추세용)
            tot, avg = self._fetch_bv_stats(p["platform_pid"])
            if tot is not None:
                self.platform_stats[("sephora_us", p["product_id"])] = {
                    "total_reviews": tot, "platform_avg_rating": avg,
                }
        return total

    def _fetch_bv_stats(self, sephora_pid: str) -> tuple:
        """BazaarVoice statistics: (전체 리뷰 수, 전체 평균 평점). 실패 시 (None, None)."""
        params = {
            "passkey":    BV_PASSKEY,
            "apiversion": "5.4",
            "Filter":     f"ProductId:{sephora_pid}",
            "Stats":      "Reviews",
        }
        for attempt in range(VOC_MAX_RETRY + 1):
            try:
                resp = requests.get(BV_STATS_URL, params=params, headers=BV_HEADERS, timeout=30)
                resp.raise_for_status()
                res = resp.json().get("Results", [])
                if res:
                    rs = res[0].get("ProductStatistics", {}).get("ReviewStatistics", {})
                    tot = rs.get("TotalReviewCount")
                    avg = rs.get("AverageOverallRating")
                    return (int(tot) if tot is not None else None,
                            round(float(avg), 3) if avg is not None else None)
                return (None, None)
            except Exception as e:
                if attempt < VOC_MAX_RETRY:
                    time.sleep(VOC_RETRY_DELAY)
                else:
                    logger.debug(f"BV stats 오류 ({sephora_pid}): {e}")
        return (None, None)

    def _fetch_bv_reviews_mixed(self, sephora_pid: str) -> list[dict]:
        """최신 10개 + 도움됨 10개 수집, 텍스트 기준 중복 제거 후 반환 (최대 20개)."""
        recent  = self._fetch_bv_single(sephora_pid, sort="SubmissionTime:desc",             limit=MAX_RECENT)
        helpful = self._fetch_bv_single(sephora_pid, sort="TotalPositiveFeedbackCount:desc", limit=MAX_HELPFUL)
        seen = {r["text"] for r in recent}
        for r in helpful:
            if r["text"] not in seen:
                recent.append(r)
                seen.add(r["text"])
        logger.info(f"  BV Reviews [{sephora_pid}]: {len(recent)}건 (최신+도움됨)")
        return recent

    def _fetch_bv_single(self, sephora_pid: str, sort: str, limit: int) -> list[dict]:
        params = {
            "Filter":     [f"ProductId:{sephora_pid}", "contentlocale:en*"],
            "Sort":       sort,
            "Limit":      limit,
            "Stats":      "Reviews",
            "passkey":    BV_PASSKEY,
            "apiversion": "5.4",
            "Locale":     "en_US",
        }
        # 일시적 실패(타임아웃/빈 응답) 대비 재시도
        last_err = None
        for attempt in range(VOC_MAX_RETRY + 1):
            try:
                resp = requests.get(BV_REVIEWS_URL, params=params,
                                    headers=BV_HEADERS, timeout=30)
                resp.raise_for_status()
                reviews = []
                for r in resp.json().get("Results", []):
                    text = r.get("ReviewText", "").strip()
                    if not text:
                        continue
                    dt_str = r.get("SubmissionTime", "")
                    try:
                        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
                        submission_time = dt.strftime("%Y/%m/%d")
                    except Exception:
                        submission_time = dt_str
                    reviews.append({
                        "rating":          float(r["Rating"]) if r.get("Rating") else None,
                        "text":            text,
                        "helpful":         r.get("TotalPositiveFeedbackCount", 0),
                        "submission_time": submission_time,
                    })
                return reviews
            except Exception as e:
                last_err = e
                if attempt < VOC_MAX_RETRY:
                    time.sleep(VOC_RETRY_DELAY)
        logger.warning(f"BV Reviews 오류 ({sephora_pid}, {VOC_MAX_RETRY+1}회 시도): {last_err}")
        return []

    # ──────────────────────────────────────────
    # OliveYoung Global — Playwright
    # ──────────────────────────────────────────
    def _collect_oy_global(self, products: list[dict], week: str) -> int:
        return asyncio.run(self._oy_collect_async(products, week))

    @staticmethod
    def _select_mixed(reviews: list[dict]) -> list[dict]:
        """스크랩 풀에서 최신 10 + 도움됨 10 선별, 중복 제거 (최대 20개)."""
        if not reviews:
            return []
        recent  = sorted(reviews, key=lambda r: r.get("submission_time") or "", reverse=True)[:MAX_RECENT]
        helpful = sorted(reviews, key=lambda r: r.get("helpful", 0), reverse=True)[:MAX_HELPFUL]
        seen = {r["text"] for r in recent}
        for r in helpful:
            if r["text"] not in seen:
                recent.append(r)
                seen.add(r["text"])
        return recent

    async def _oy_collect_async(self, products: list[dict], week: str) -> int:
        UA = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
        total = 0
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled"],
            )

            # Page Pool (3개 병렬)
            POOL = 3
            pages = []
            for _ in range(POOL):
                ctx  = await browser.new_context(
                    viewport={"width": 1280, "height": 900},
                    user_agent=UA,
                )
                pages.append(await ctx.new_page())

            queue: asyncio.Queue = asyncio.Queue()
            for pg in pages:
                await queue.put(pg)

            async def collect_one(product_id: str, prdtno: str) -> list[dict]:
                pg = await queue.get()
                reviews: list[dict] = []
                try:
                    # 빈 결과면 재시도 (일시적 로딩 실패 대비)
                    for attempt in range(VOC_MAX_RETRY + 1):
                        try:
                            raw     = await self._scrape_oy_reviews(pg, prdtno)
                            reviews = self._select_mixed(raw)  # 최신10 + 도움됨10
                        except Exception as e:
                            logger.debug(f"OY VOC 오류 ({prdtno}, 시도 {attempt+1}): {e}")
                            reviews = []
                        if reviews:
                            break
                        if attempt < VOC_MAX_RETRY:
                            await pg.wait_for_timeout(VOC_RETRY_DELAY * 1000)
                finally:
                    await queue.put(pg)
                return reviews

            tasks = [collect_one(p["product_id"], p["platform_pid"]) for p in products]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for p, reviews in zip(products, results):
                if isinstance(reviews, list):
                    total += self._save_reviews(reviews, week, "oy_global", p["product_id"])
                # 전체 통계 매핑 (prdtno → product_id)
                stat = self._oy_stats.get(p["platform_pid"])
                if stat:
                    self.platform_stats[("oy_global", p["product_id"])] = {
                        "total_reviews": stat[0], "platform_avg_rating": stat[1],
                    }

            await browser.close()
        return total

    async def _extract_oy_stats(self, page, prdtno: str) -> None:
        """OY 상세 페이지에서 전체 리뷰 수 + 평균 평점 추출 → self._oy_stats."""
        total_reviews = None
        avg_rating = None
        # 총 리뷰 수: 'Review (1,234)' 류 텍스트에서 숫자 추출
        for sel in ['[class*="review"] [class*="count"]', '.review-count',
                    'a[href*="review"]', '[class*="reviewCount"]', '.tab-review']:
            try:
                el = await page.query_selector(sel)
                if el:
                    txt = (await el.inner_text()).replace(",", "")
                    m = re.search(r'(\d{1,7})', txt)
                    if m:
                        total_reviews = int(m.group(1))
                        break
            except Exception:
                pass
        # 평균 평점: em.star-rating > span 의 width:% (100%=5점) 우선
        #   OY 구조 예: <em class="star-rating"><span style="width:96%"></span></em><span>4.8</span>
        try:
            span = await page.query_selector('em.star-rating > span, .star-rating > span')
            if span:
                style = (await span.get_attribute("style")) or ""
                wm = re.search(r'width:\s*([\d.]+)%', style)
                if wm:
                    avg_rating = round(float(wm.group(1)) / 20.0, 3)
        except Exception:
            pass
        # 폴백: 텍스트 평점 (별점 옆 숫자 등)
        if avg_rating is None:
            for sel in ['.star-rating + span', '.rating-score', '.star-point',
                        '[class*="rating"] [class*="score"]', '[class*="avgScore"]']:
                try:
                    el = await page.query_selector(sel)
                    if el:
                        txt = (await el.inner_text() or "").strip()
                        m = re.search(r'([0-5](?:\.\d+)?)', txt)
                        if m:
                            avg_rating = round(float(m.group(1)), 3)
                            break
                except Exception:
                    pass
        if total_reviews is not None or avg_rating is not None:
            self._oy_stats[prdtno] = (total_reviews, avg_rating)

    async def _scrape_oy_reviews(self, page, prdtno: str) -> list[dict]:
        """OY Global 제품 상세 페이지에서 리뷰 스크래핑."""
        url = OY_BASE_URL + OY_PRODUCT_URL + prdtno
        reviews = []
        seen_texts = set()

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=40_000)
            await page.wait_for_timeout(4000)

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

            # 플랫폼 전체 통계 추출 (총 리뷰 수 + 평균 평점)
            await self._extract_oy_stats(page, prdtno)
        except Exception as e:
            logger.warning(f"OY VOC 페이지 접속 실패 ({prdtno}): {e}")
            return []

        no_new_count = 0
        for _ in range(30):
            if len(reviews) >= MAX_COLLECT:
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
                if len(reviews) >= MAX_COLLECT:
                    break
                try:
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

                    rating = 0.0
                    try:
                        container   = await item.query_selector('.review-star-rating') or item
                        star_wraps  = await container.query_selector_all('.wrap-icon-star')
                        if star_wraps:
                            rating = sum(
                                1.0 if len(await sw.query_selector_all('.icon-star.filled')) >= 2
                                else 0.5 if len(await sw.query_selector_all('.icon-star.filled')) == 1
                                else 0
                                for sw in star_wraps
                            )
                    except Exception:
                        pass

                    date_str = ""
                    for d_sel in ['.review-info .date', '.review-write-info-date', '.date', 'time']:
                        el = await item.query_selector(d_sel)
                        if el:
                            date_str = (await el.inner_text()).strip()
                            if date_str:
                                break

                    helpful = 0
                    for h_sel in ['.btn-helpful .count', '.helpful-count']:
                        el = await item.query_selector(h_sel)
                        if el:
                            digits = ''.join(filter(str.isdigit, await el.inner_text()))
                            helpful = int(digits) if digits else 0
                            if helpful:
                                break

                    reviews.append({
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

        logger.info(f"  OY VOC [{prdtno}]: {len(reviews)}건")
        return reviews

    # ──────────────────────────────────────────
    # 공통: DB 저장
    # ──────────────────────────────────────────
    def _save_reviews(self, reviews: list[dict], week: str,
                      platform_id: str, product_id: str) -> int:
        count = 0
        with self._conn() as conn:
            for r in reviews:
                if not r.get("text"):
                    continue
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO fact_raw_reviews
                            (week, platform_id, product_id,
                             rating, review_text, helpful, submission_time)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (
                        week, platform_id, product_id,
                        r.get("rating"),
                        r["text"],
                        r.get("helpful", 0),
                        r.get("submission_time"),
                    ))
                    count += conn.execute("SELECT changes()").fetchone()[0]
                except Exception:
                    pass
        return count
