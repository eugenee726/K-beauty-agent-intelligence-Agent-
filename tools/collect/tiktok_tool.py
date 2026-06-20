"""
tiktok_tool.py
───────────────
Apify 기반 TikTok K-beauty 트렌드 수집 툴.

수집 전략:
  1단계: K-beauty 해시태그로 영상 수집 (YouTube와 동일 — 수집 시각 기준 최근 7일)
  2단계: createTime 2차 검증 + 캡션 K-beauty 키워드 매칭
  3단계: engagement threshold 이상만 DB 저장

노이즈 해결 방식:
  해시태그만 보는 게 아니라 캡션 텍스트를 직접 분석.
  → "18thbirthday", "100kviews" 같은 노이즈 자동 제거.
"""

import json
import re
import sqlite3
import logging
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class TikTokTool:
    """
    Apify TikTok Scraper 연동 수집 툴.
    Collection Agent가 호출.

    미국 타겟 수집:
      1차) proxyCountryCode="US" — 미국 IP로 접속
      2차) textLanguage=="en" 필터 — 비영어 게시물 제거
      (YouTube의 regionCode="US"+relevanceLanguage="en"과 동일 취지)
    """

    PLATFORM_ID = "tiktok"
    RECENT_DAYS = 7   # YouTube publishedAfter와 동일 — 수집 시각 기준 롤링 7일

    # ── 수집 대상 해시태그 ─────────────────────────────
    # 핵심 K-beauty 해시태그만 유지 (크레딧 절약)
    # 16개 × 200 → 6개 × 50 : 회당 수집량 ~97% 감소
    TARGET_HASHTAGS: list[str] = [
        # 일반 K-beauty
        "kbeauty",
        "koreanskincare",
        "kbeautyskincare",
        "oliveyoung",
        "koreanbeauty",
        "skincareroutine",
        # AP 브랜드 전체 (dim_brand 기준)
        "sulwhasoo",
        "hera",
        "primera",
        "tataharper",
        "iope",
        "aestura",
        "mamonde",
        "hanyul",
        "laneige",
        "innisfree",
        "cosrx",
        "espoir",
        "etude",
    ]

    # ── K-beauty 키워드 사전 (2단계 노이즈 필터) ─────────
    # 캡션에 아래 키워드 중 하나 이상 있어야 유효한 포스트로 판단.
    KBEAUTY_TERMS: set[str] = {
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
        # 브랜드
        "cosrx", "laneige", "innisfree", "sulwhasoo", "hera",
        "some by mi", "beauty of joseon", "torriden", "anua",
        "isntree", "skin1004", "numbuzin", "round lab", "klairs",
        "tirtir", "romand", "peripera", "dr jart", "iope",
        "primera", "aestura", "mamonde", "hanyul", "espoir", "etude",
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

    def __init__(self, db_path: str, apify_token: str, min_engagement: float = 50.0):
        self.db_path = db_path
        self.apify_token = apify_token
        self.min_engagement = min_engagement

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    # ──────────────────────────────────────────
    # 1단계: Apify로 TikTok 영상 수집 (최근 7일)
    # ──────────────────────────────────────────
    def _current_week(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%G-W%V")

    @classmethod
    def _published_after_cutoff(cls, days: int | None = None) -> datetime:
        """YouTube publishedAfter와 동일 — UTC 기준 수집 시각 - N일."""
        days = days if days is not None else cls.RECENT_DAYS
        return datetime.now(tz=timezone.utc) - timedelta(days=days)

    @classmethod
    def _published_after_date(cls, days: int | None = None) -> str:
        """Apify oldestPostDateUnified용 (YYYY-MM-DD)."""
        return cls._published_after_cutoff(days).strftime("%Y-%m-%d")

    @staticmethod
    def _parse_post_time(video: dict) -> datetime | None:
        iso = video.get("createTimeISO")
        if iso:
            try:
                return datetime.fromisoformat(iso.replace("Z", "+00:00"))
            except ValueError:
                pass
        ts = video.get("createTime")
        if ts is not None:
            try:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc)
            except (TypeError, ValueError, OSError):
                pass
        return None

    def _is_recent_post(self, video: dict, cutoff: datetime) -> bool:
        """YouTube publishedAfter와 동일 창 — 게시 시각이 cutoff 이후인지."""
        posted = self._parse_post_time(video)
        if posted is None:
            return False
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        return posted >= cutoff

    def _fetch_from_apify(
        self, hashtag: str, max_items: int = 200, published_after: str = None
    ) -> list[dict]:
        """
        Apify TikTok Scraper Actor 호출.
        Actor: clockworks~tiktok-scraper
        published_after: YYYY-MM-DD — 이 날짜 이후 게시물만 요청 (YouTube 7일 창).
        """
        url = "https://api.apify.com/v2/acts/clockworks~tiktok-scraper/run-sync-get-dataset-items?memory=256"
        headers = {"Authorization": f"Bearer {self.apify_token}"}
        payload = {
            "hashtags":                    [hashtag],
            "resultsPerPage":              max_items,
            "commentsPerPost":             0,
            "maxRepliesPerComment":        0,
            "excludePinnedPosts":          False,
            "scrapeRelatedVideos":         False,
            "shouldDownloadVideos":        False,
            "shouldDownloadCovers":        False,
            "shouldDownloadAvatars":       False,
            "shouldDownloadSubtitles":     False,
            "shouldDownloadMusicCovers":   False,
            "shouldDownloadSlideshowImages": False,
            "proxyCountryCode":            "US",   # 미국 IP로 접속 (미국 타겟)
        }
        if published_after:
            payload["oldestPostDateUnified"] = published_after

        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=120)
            if resp.status_code not in (200, 201):  # 201 = Created (정상)
                logger.warning(f"Apify 오류 #{hashtag}: {resp.status_code}")
                return []
            return resp.json()
        except Exception as e:
            logger.warning(f"Apify 수집 실패 #{hashtag}: {e}")
            return []

    # ──────────────────────────────────────────
    # 2단계: 캡션 기반 K-beauty 키워드 추출 (노이즈 필터)
    # ──────────────────────────────────────────
    def _extract_keywords(self, caption: str, hashtags: list[str]) -> list[str]:
        """
        캡션 + 해시태그 텍스트에서 K-beauty 키워드 추출.
        KBEAUTY_TERMS에 없으면 제거 → 노이즈 원천 차단.
        """
        text = (caption + " " + " ".join(hashtags or [])).lower()
        found = set()

        for term in self.KBEAUTY_TERMS:
            pattern = r'\b' + re.escape(term) + r'\b'
            if re.search(pattern, text):
                normalized = re.sub(r'[\s\-&]+', '_', term.strip())
                found.add(normalized)

        return list(found)

    # ──────────────────────────────────────────
    # 3단계: 집계 + DB 저장
    # ──────────────────────────────────────────
    def fetch_and_store(self, week: str = None) -> int:
        """
        Collection Agent 호출 인터페이스.
        전체 파이프라인: 수집 → 필터링 → 집계 → DB 저장.
        반환: 저장된 신규 레코드 수
        """
        week = week or self._current_week()
        cutoff = self._published_after_cutoff()
        published_after = self._published_after_date()
        logger.info(
            f"TikTok 수집 기간: 최근 {self.RECENT_DAYS}일 "
            f"({published_after} 이후, YouTube publishedAfter와 동일)"
        )

        all_records  = []
        raw_records  = []
        skipped_old  = 0

        skipped_lang = 0
        for hashtag in self.TARGET_HASHTAGS:
            logger.info(f"TikTok 수집: #{hashtag}")
            videos = self._fetch_from_apify(
                hashtag, max_items=40, published_after=published_after
            )
            logger.info(f"  #{hashtag}: {len(videos)}개 영상 수집")

            for video in videos:
                if not self._is_recent_post(video, cutoff):
                    skipped_old += 1
                    continue

                # 미국 타겟: 영어 콘텐츠만 (textLanguage 없으면 통과시켜 누락 방지)
                lang = video.get("textLanguage")
                if lang and lang != "en":
                    skipped_lang += 1
                    continue

                # Apify 응답 필드 파싱
                caption   = video.get("text", "") or ""
                tags      = [t.get("name", "") for t in video.get("hashtags", [])]
                views     = int(video.get("playCount",    0) or 0)
                likes     = int(video.get("diggCount",    0) or 0)
                comments  = int(video.get("commentCount", 0) or 0)
                post_id   = video.get("id", "") or video.get("webVideoUrl", "")
                engagement = views * 0.3 + likes * 1.0 + comments * 2.0

                if engagement < self.min_engagement:
                    continue

                keywords = self._extract_keywords(caption, tags)
                if not keywords:
                    continue

                # raw 원문 저장용 레코드
                if caption and post_id:
                    raw_records.append({
                        "week":         week,
                        "post_id":      str(post_id),
                        "content_text": caption,
                        "hashtags":     json.dumps(tags),
                        "view_count":   views,
                        "like_count":   likes,
                    })

                for kw in keywords:
                    all_records.append({
                        "week":             week,
                        "keyword":          kw,
                        "total_views":      views,
                        "total_likes":      likes,
                        "total_comments":   comments,
                        "engagement_score": engagement,
                    })

        if skipped_old:
            logger.info(f"TikTok createTime 필터 제외: {skipped_old}건 (7일 이전)")
        if skipped_lang:
            logger.info(f"TikTok 언어 필터 제외: {skipped_lang}건 (비영어)")

        if not all_records:
            logger.warning("TikTok: 수집된 유효 레코드 없음")
            return 0

        # raw 원문 저장
        if raw_records:
            with self._conn() as conn:
                for r in raw_records:
                    try:
                        conn.execute("""
                            INSERT OR IGNORE INTO fact_sns_raw
                                (week, platform_id, post_id, content_text, hashtags,
                                 view_count, like_count)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (week, self.PLATFORM_ID, r["post_id"], r["content_text"],
                              r["hashtags"], r["view_count"], r["like_count"]))
                    except Exception:
                        pass
            logger.info(f"TikTok raw 저장: {len(raw_records)}건")

        # 주차 × 키워드 집계
        df = pd.DataFrame(all_records)
        weekly = (
            df.groupby(["week", "keyword"])
            .agg(
                post_count      = ("engagement_score", "count"),
                total_views     = ("total_views",      "sum"),
                total_likes     = ("total_likes",      "sum"),
                total_comments  = ("total_comments",   "sum"),
                engagement_score= ("engagement_score", "sum"),
            )
            .reset_index()
        )

        # 전주 대비 성장률
        weekly = weekly.sort_values(["keyword", "week"])
        weekly["growth_rate"] = (
            weekly.groupby("keyword")["engagement_score"]
            .pct_change() * 100
        ).round(1)

        # DB 저장
        inserted = 0
        with self._conn() as conn:
            # 이번 주 기존 데이터 확인
            existing = set(
                row[0]
                for row in conn.execute(
                    "SELECT keyword FROM fact_sns_signals WHERE platform_id=? AND week=?",
                    (self.PLATFORM_ID, week),
                ).fetchall()
            )

            for _, row in weekly.iterrows():
                if row["keyword"] in existing:
                    continue

                # 첫 등장 여부
                is_new = 1 if not conn.execute(
                    "SELECT 1 FROM fact_sns_signals WHERE platform_id=? AND keyword=?",
                    (self.PLATFORM_ID, row["keyword"])
                ).fetchone() else 0

                conn.execute("""
                    INSERT OR IGNORE INTO fact_sns_signals
                        (week, platform_id, keyword, post_count,
                         total_views, total_likes, total_comments,
                         engagement_score, growth_rate, is_new_keyword)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    row["week"],
                    self.PLATFORM_ID,
                    row["keyword"],
                    int(row["post_count"]),
                    int(row["total_views"]),
                    int(row["total_likes"]),
                    int(row["total_comments"]),
                    float(row["engagement_score"]),
                    float(row["growth_rate"]) if pd.notna(row["growth_rate"]) else None,
                    is_new,
                ))
                inserted += conn.execute("SELECT changes()").fetchone()[0]

        logger.info(f"TikTok 신호 저장: {inserted}건 (week={week})")
        return inserted