"""
youtube_tool.py
────────────────
YouTube Data API v3 기반 K-beauty 트렌드 수집 툴.

수집 전략:
  1단계: K-beauty 검색어 목록으로 영상 검색 (최근 7일)
  2단계: 영상 통계(조회수/좋아요/댓글) 일괄 조회
  3단계: 캡션 기반 KBEAUTY_TERMS 키워드 추출 (TikTok과 동일 사전)
  4단계: engagement_score 계산 후 fact_sns_signals 저장
         YouTube: views*0.2 + likes*1.5 + comments*3.0
"""

import json
import re
import sqlite3
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


class YouTubeTool:
    """YouTube Data API v3 K-beauty 수집 툴."""

    PLATFORM_ID = "youtube"

    # ── 검색 쿼리 목록 ──────────────────────────────────
    TARGET_QUERIES: list[str] = [
        # 일반 K-beauty
        "kbeauty", "k-beauty skincare", "korean skincare routine",
        "korean beauty", "kbeauty review", "kbeauty haul", "oliveyoung", 
        "skincare routine", "skincare product", "skincare review", "skincare haul",
        # AP 브랜드 전체 (dim_brand 기준)
        "cosrx review", "laneige review", "innisfree review",
        "sulwhasoo review", "hera skincare", "primera skincare",
        "tata harper review", "iope skincare", "aestura skincare",
        "mamonde review", "hanyul skincare", "espoir review",
        "etude house review",
    ]

    # ── K-beauty 키워드 사전 (TikTok과 동일) ───────────
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

    def __init__(self, db_path: str, api_key: str, min_engagement: float = 100.0):
        self.db_path = db_path
        self.api_key = api_key
        self.min_engagement = min_engagement
        self._youtube = None

    def _get_client(self):
        if self._youtube is None:
            self._youtube = build("youtube", "v3", developerKey=self.api_key)
        return self._youtube

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _current_week(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%G-W%V")

    # ──────────────────────────────────────────
    # 1단계: 검색어로 영상 ID 목록 수집 (최근 7일)
    # ──────────────────────────────────────────
    def _search_videos(self, query: str, max_results: int = 40) -> list[str]:
        """YouTube Search API → 영상 ID 목록 반환."""
        yt = self._get_client()
        published_after = (
            datetime.now(tz=timezone.utc) - timedelta(days=7)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            resp = yt.search().list(
                q=query,
                part="id",
                type="video",
                maxResults=min(max_results, 50),
                publishedAfter=published_after,
                relevanceLanguage="en",
                regionCode="US",
            ).execute()
            return [
                item["id"]["videoId"]
                for item in resp.get("items", [])
                if item["id"]["kind"] == "youtube#video"
            ]
        except Exception as e:
            logger.warning(f"YouTube 검색 실패 ({query}): {e}")
            return []

    # ──────────────────────────────────────────
    # 2단계: 영상 통계 일괄 조회 (최대 50개씩)
    # ──────────────────────────────────────────
    def _fetch_video_stats(self, video_ids: list[str]) -> list[dict]:
        """Video statistics API → 통계 + 제목/설명 반환."""
        if not video_ids:
            return []
        yt = self._get_client()
        results = []

        # 50개씩 배치 처리
        for i in range(0, len(video_ids), 50):
            batch = video_ids[i:i+50]
            try:
                resp = yt.videos().list(
                    id=",".join(batch),
                    part="snippet,statistics",
                ).execute()
                for item in resp.get("items", []):
                    stats   = item.get("statistics", {})
                    snippet = item.get("snippet", {})
                    results.append({
                        "video_id":    item["id"],
                        "title":       snippet.get("title", ""),
                        "description": snippet.get("description", "")[:500],
                        "tags":        snippet.get("tags", []),
                        "views":       int(stats.get("viewCount",    0) or 0),
                        "likes":       int(stats.get("likeCount",    0) or 0),
                        "comments":    int(stats.get("commentCount", 0) or 0),
                    })
            except Exception as e:
                logger.warning(f"YouTube 통계 조회 실패: {e}")
        return results

    # ──────────────────────────────────────────
    # 3단계: 텍스트 기반 K-beauty 키워드 추출
    # ──────────────────────────────────────────
    def _extract_keywords(self, title: str, description: str, tags: list[str]) -> list[str]:
        text = (title + " " + description + " " + " ".join(tags or [])).lower()
        found = set()
        for term in self.KBEAUTY_TERMS:
            pattern = r'\b' + re.escape(term) + r'\b'
            if re.search(pattern, text):
                normalized = re.sub(r'[\s\-&]+', '_', term.strip())
                found.add(normalized)
        return list(found)

    # ──────────────────────────────────────────
    # 전체 파이프라인: 수집 → 집계 → DB 저장
    # ──────────────────────────────────────────
    def fetch_and_store(self, week: str = None) -> int:
        """
        Collection Agent 호출 인터페이스.
        반환: 저장된 신규 레코드 수
        """
        week = week or self._current_week()
        all_records: list[dict] = []
        raw_records: list[dict] = []

        seen_video_ids: set[str] = set()

        for query in self.TARGET_QUERIES:
            logger.info(f"YouTube 검색: {query!r}")
            video_ids = self._search_videos(query)

            new_ids = [v for v in video_ids if v not in seen_video_ids]
            seen_video_ids.update(new_ids)

            videos = self._fetch_video_stats(new_ids)
            logger.info(f"  {query!r}: {len(videos)}개 영상 통계 조회")

            for v in videos:
                engagement = v["views"] * 0.2 + v["likes"] * 1.5 + v["comments"] * 3.0
                if engagement < self.min_engagement:
                    continue

                keywords = self._extract_keywords(
                    v["title"], v["description"], v["tags"]
                )
                if not keywords:
                    continue

                # raw 원문 저장용 레코드 (제목 + 설명 합쳐서 저장)
                content_text = f"{v['title']} {v['description']}".strip()
                if content_text and v["video_id"]:
                    raw_records.append({
                        "post_id":      v["video_id"],
                        "content_text": content_text,
                        "hashtags":     json.dumps(v.get("tags", [])),
                        "view_count":   v["views"],
                        "like_count":   v["likes"],
                    })

                for kw in keywords:
                    all_records.append({
                        "week":             week,
                        "keyword":          kw,
                        "total_views":      v["views"],
                        "total_likes":      v["likes"],
                        "total_comments":   v["comments"],
                        "engagement_score": engagement,
                    })

        if not all_records:
            logger.warning("YouTube: 유효 레코드 없음")
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
            logger.info(f"YouTube raw 저장: {len(raw_records)}건")

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

        weekly = weekly.sort_values(["keyword", "week"])
        weekly["growth_rate"] = (
            weekly.groupby("keyword")["engagement_score"]
            .pct_change() * 100
        ).round(1)

        inserted = 0
        with self._conn() as conn:
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

        logger.info(f"YouTube 신호 저장: {inserted}건 (week={week})")
        return inserted
