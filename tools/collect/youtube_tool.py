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

from tools.collect.sns_terms import kbeauty_terms, target_queries

logger = logging.getLogger(__name__)


class YouTubeTool:
    """YouTube Data API v3 K-beauty 수집 툴."""

    PLATFORM_ID = "youtube"

    def __init__(self, db_path: str, api_key: str, min_engagement: float = 100.0):
        self.db_path = db_path
        # 수집 대상·키워드 사전은 dim_brand 기반으로 만든다 (tools/collect/sns_terms.py)
        self.TARGET_QUERIES = target_queries(db_path)
        self.KBEAUTY_TERMS  = kbeauty_terms(db_path)
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
    def _fetch_video_stats(self, video_ids: list[str], failed: list | None = None) -> list[dict]:
        """
        Video statistics API → 통계 + 제목/설명 반환.
        failed 를 넘기면 조회에 실패한 배치(할당량 소진 등)의 영상 ID 를 담아 준다.
        (응답에 없는 영상 = 삭제·비공개는 실패가 아니라 결과에서 빠질 뿐이다)
        """
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
                if failed is not None:
                    failed.extend(batch)
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
    # 일간 수집: 하루 단위 날짜 창으로 검색 (탐지 표본 확대, 무료 할당 안)
    # ──────────────────────────────────────────
    DAILY_LOG_SQL = """CREATE TABLE IF NOT EXISTS yt_daily_log (
        day TEXT PRIMARY KEY, queries INTEGER, stored INTEGER, collected_at TEXT DEFAULT (datetime('now')))"""

    @staticmethod
    def run_week_of(day) -> str:
        """day 를 분석하는 주간 실행일(그 다음 목요일)의 ISO 주차. 목요일 당일 게시물은 다음 주 실행에 들어간다."""
        ahead = (3 - day.weekday()) % 7 or 7
        return (day + timedelta(days=ahead)).strftime("%G-W%V")

    def collect_day(self, day) -> int:
        """
        day(UTC 날짜)에 올라온 영상만 검색어별 1페이지 수집해 fact_sns_raw 에 저장한다.
        날짜 창이 겹치지 않고 (platform_id, post_id) UNIQUE 라 날마다 중복되지 않는다.
        week 는 그 날을 분석하는 주간 실행(다음 목요일)의 ISO 주차 — 예전 주간 수집처럼 '목~수 7일'이 한 주.
        통계·신호는 주간 집계(aggregate_week)에서 다시 계산한다.
        """
        from datetime import date as _date
        day = day if isinstance(day, _date) else datetime.strptime(str(day), "%Y-%m-%d").date()
        after = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        week = self.run_week_of(day)
        stored, n_q = self._collect_window(after, after + timedelta(days=1), week)
        with self._conn() as conn:
            conn.execute(self.DAILY_LOG_SQL)
            if n_q == len(self.TARGET_QUERIES):   # 끝까지 수집한 날만 완료로 기록
                conn.execute("INSERT OR REPLACE INTO yt_daily_log (day, queries, stored) VALUES (?, ?, ?)",
                             (day.isoformat(), n_q, stored))
        logger.info(f"YouTube 일간 수집 {day} (week={week}): 검색어 {n_q}개, 신규 저장 {stored}건")
        return stored

    def collect_gap(self, days: list, week: str) -> int:
        """
        일간 수집이 빠진 여러 날을 검색어당 1번의 기간 검색으로 채운다 (할당 약 5,400단위로 고정).
        하루씩 채우면 빠진 날 × 5,400단위라 이틀만 빠져도 무료 할당(10,000)을 넘는다.
        기간 전체에서 검색어당 50개라 하루씩 모을 때보다 표본은 성기다. 일간 완료 기록은 남기지 않는다.
        """
        first, last = min(days), max(days)
        after = datetime(first.year, first.month, first.day, tzinfo=timezone.utc)
        before = datetime(last.year, last.month, last.day, tzinfo=timezone.utc) + timedelta(days=1)
        stored, n_q = self._collect_window(after, before, week)
        logger.warning(f"YouTube 일간 수집 누락 {len(days)}일({first}~{last}) → 기간 검색으로 보충: "
                       f"검색어 {n_q}개, 신규 저장 {stored}건 (일간보다 표본 적음)")
        return stored

    def _collect_window(self, after: datetime, before: datetime, week: str) -> tuple[int, int]:
        """[after, before) 에 올라온 영상을 검색어별 1페이지 수집해 fact_sns_raw 에 저장. (신규 저장 수, 완료 검색어 수)"""
        yt = self._get_client()
        ids, seen, n_q = [], set(), 0
        for query in self.TARGET_QUERIES:
            try:
                resp = yt.search().list(
                    q=query, part="id", type="video", maxResults=50,
                    publishedAfter=after.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    publishedBefore=before.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    relevanceLanguage="en", regionCode="US",
                ).execute()
                n_q += 1
            except Exception as e:
                if "quota" in str(e).lower():
                    logger.warning(f"YouTube 할당량 소진 — {after:%Y-%m-%d}~ 수집 중단 ({n_q}개 검색어까지)")
                    break
                logger.warning(f"YouTube 검색 실패 {query!r}: {e}")
                continue
            for it in resp.get("items", []):
                vid = it.get("id", {}).get("videoId")
                if vid and vid not in seen:
                    seen.add(vid)
                    ids.append(vid)
        stored = 0
        with self._conn() as conn:
            for v in self._fetch_video_stats(ids):
                engagement = v["views"] * 0.2 + v["likes"] * 1.5 + v["comments"] * 3.0
                # 원문은 사전과 무관하게 저장한다 (나중에 추가한 용어를 과거 원문에서 정확히 다시 세고, 사전 밖 용어 후보를 찾기 위해).
                # 키워드 신호·탐지 분모는 사전 용어가 있는 게시물만 쓴다 (aggregate_week, sns_terms.dictionary_post_counts).
                if engagement < self.min_engagement:
                    continue
                content_text = f"{v['title']} {v['description']}".strip()
                cur = conn.execute("""INSERT OR IGNORE INTO fact_sns_raw
                        (week, platform_id, post_id, content_text, hashtags, view_count, like_count)
                        VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (week, self.PLATFORM_ID, v["video_id"], content_text, json.dumps(v.get("tags", [])),
                     v["views"], v["likes"]))
                stored += cur.rowcount
        return stored, n_q

    def aggregate_week(self, week: str) -> int:
        """
        그 주 YouTube 원문 전체의 통계를 한 번에 다시 조회해(측정 시점 통일) 키워드 신호를 다시 만든다.
        fact_sns_signals(youtube, week) 를 지우고 새로 쓴다.
        """
        with self._conn() as conn:
            raw = {r["post_id"]: r for r in conn.execute(
                "SELECT post_id, content_text, hashtags, view_count, like_count FROM fact_sns_raw "
                "WHERE week=? AND platform_id=?", (week, self.PLATFORM_ID))}
        failed: list[str] = []
        fresh = self._fetch_video_stats(list(raw), failed=failed)
        # 통계 조회에 실패한 영상(할당량 소진 등)은 저장돼 있던 값으로 집계한다 — 0건으로 끝나 파이프라인이 멈추지 않게.
        # 댓글 수는 원문 테이블에 없어 0 으로 본다. 측정 시점이 섞이므로 경고로 남긴다.
        stale = []
        for pid in failed:
            r = raw[pid]
            try:
                tags = json.loads(r["hashtags"] or "[]")
            except (TypeError, ValueError):
                tags = []
            stale.append({"video_id": pid, "title": r["content_text"] or "", "description": "", "tags": tags,
                          "views": r["view_count"] or 0, "likes": r["like_count"] or 0, "comments": 0})
        if stale:
            logger.warning(f"YouTube 주간 집계 {week}: 통계 조회 실패 {len(stale)}개는 저장된 조회수·좋아요로 집계 (댓글 0)")
        records = []
        with self._conn() as conn:
            for v in fresh:
                conn.execute("UPDATE fact_sns_raw SET view_count=?, like_count=? WHERE platform_id=? AND post_id=?",
                             (v["views"], v["likes"], self.PLATFORM_ID, v["video_id"]))
            for v in fresh + stale:
                engagement = v["views"] * 0.2 + v["likes"] * 1.5 + v["comments"] * 3.0
                if engagement < self.min_engagement:
                    continue
                for kw in self._extract_keywords(v["title"], v["description"], v["tags"]):
                    records.append({"keyword": kw, "views": v["views"], "likes": v["likes"],
                                    "comments": v["comments"], "eng": engagement})
        if not records:
            logger.warning(f"YouTube 주간 집계 {week}: 유효 레코드 없음")
            return 0
        agg = pd.DataFrame(records).groupby("keyword").agg(
            post_count=("eng", "count"), total_views=("views", "sum"), total_likes=("likes", "sum"),
            total_comments=("comments", "sum"), engagement_score=("eng", "sum")).reset_index()
        with self._conn() as conn:
            conn.execute("DELETE FROM fact_sns_signals WHERE week=? AND platform_id=?", (week, self.PLATFORM_ID))
            for _, r in agg.iterrows():
                is_new = 0 if conn.execute(
                    "SELECT 1 FROM fact_sns_signals WHERE platform_id=? AND keyword=? AND week<?",
                    (self.PLATFORM_ID, r["keyword"], week)).fetchone() else 1
                conn.execute("""INSERT INTO fact_sns_signals
                        (week, platform_id, keyword, post_count, total_views, total_likes, total_comments,
                         engagement_score, growth_rate, is_new_keyword)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
                    (week, self.PLATFORM_ID, r["keyword"], int(r["post_count"]), int(r["total_views"]),
                     int(r["total_likes"]), int(r["total_comments"]), float(r["engagement_score"]), is_new))
        logger.info(f"YouTube 주간 집계 {week}: 영상 {len(raw)}개 → 키워드 {len(agg)}개")
        return len(agg)

    def fetch_and_store(self, week: str = None) -> int:
        """
        주간 수집 인터페이스 (Airflow collect_youtube).
        이번 주에 일간 수집이 빠진 날을 채운 뒤 주간 집계를 다시 만든다.
        빠진 날이 하루면 일간 수집 1회, 여러 날이면 기간 검색 1회(collect_gap)라 할당은 약 5,400단위를 넘지 않는다.
        일간 수집 기록이 전혀 없으면 예전 방식(최근 7일 검색)으로 수집한다.
        """
        week = week or self._current_week()
        with self._conn() as conn:
            conn.execute(self.DAILY_LOG_SQL)
            done = {r[0] for r in conn.execute("SELECT day FROM yt_daily_log")}
        y, w = week.split("-W")
        thursday = datetime.fromisocalendar(int(y), int(w), 4).date()   # 그 주 실행일
        today = datetime.now(tz=timezone.utc).date()
        days = [thursday - timedelta(days=i) for i in range(7, 0, -1) if thursday - timedelta(days=i) < today]
        if any(d.isoformat() in done for d in days):
            missing = [d for d in days if d.isoformat() not in done]
            if len(missing) == 1:
                self.collect_day(missing[0])
            elif missing:
                self.collect_gap(missing, week)
            return self.aggregate_week(week)
        return self._fetch_and_store_weekly(week)

    # 예전 방식: 최근 7일 검색 1회 (일간 수집 도입 전 주차, 일간 기록이 없을 때)
    def _fetch_and_store_weekly(self, week: str = None) -> int:
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

                # raw 원문 저장용 레코드 (제목 + 설명 합쳐서 저장) — 사전 용어가 없어도 저장 (_collect_window 주석 참고)
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
