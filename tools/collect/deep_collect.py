"""
deep_collect.py
────────────────
트렌드 2차 수집: 분석 단계에서 트렌드 키워드가 정해진 뒤, 키워드별로 캡션을 더 모은다.

왜: 주간 1차 수집(브랜드·일반 해시태그/검색어)만으로는 트렌드 키워드가 들어간 게시물이
    5~37건뿐이라, 소비자 니즈 해석과 브랜드 언급 집계의 표본이 너무 작다.

- YouTube: "{키워드} korean skincare" 검색 2페이지(최대 100건). 무료 API 할당 안에서 동작.
- TikTok: 키워드 해시태그(#eyecream 등) 15건. Apify 결과 건당 과금(약 $4~5/1,000건)이라 소량만.
- 저장: fact_sns_deep (fact_sns_raw 와 분리). 트렌드 탐지(z-score)와 KPI 게시물 수에는 쓰지 않고,
  LLM 캡션 샘플과 결정적 브랜드 집계에만 쓴다.
- 1차 수집과 같은 필터: 최근 7일, 미국(US)·영어, 본문에 키워드 포함.
"""

import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS fact_sns_deep (
    week          TEXT NOT NULL,
    keyword       TEXT NOT NULL,      -- 2차 수집을 촉발한 트렌드 키워드
    platform_id   TEXT NOT NULL,      -- 'youtube' | 'tiktok'
    post_id       TEXT NOT NULL,
    content_text  TEXT,
    hashtags      TEXT,
    view_count    INTEGER DEFAULT 0,
    like_count    INTEGER DEFAULT 0,
    collected_at  TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (week, keyword, platform_id, post_id)
)
"""


class TrendDeepCollector:
    YT_PAGES = int(os.getenv("DEEP_YT_PAGES", "2"))       # 페이지당 50건
    TT_ITEMS = int(os.getenv("DEEP_TT_ITEMS", "15"))   # Apify 월 한도($19) 안: 1차 6×60 + 28×10 + 2차 10×15 ≈ 주 790건
    RECENT_DAYS = 7

    def __init__(self, db_path: str, youtube_api_key: str | None = None, apify_token: str | None = None):
        self.db_path = db_path
        self.youtube_api_key = youtube_api_key or os.getenv("YOUTUBE_API_KEY")
        self.apify_token = apify_token or os.getenv("APIFY_TOKEN")

    # ── YouTube ──
    def _youtube(self, kw: str) -> list[dict]:
        if not self.youtube_api_key:
            return []
        from tools.collect.youtube_tool import YouTubeTool
        yt_tool = YouTubeTool(self.db_path, self.youtube_api_key)
        yt = yt_tool._get_client()
        plain = kw.replace("_", " ")
        after = (datetime.now(tz=timezone.utc) - timedelta(days=self.RECENT_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ids, token = [], None
        for _ in range(self.YT_PAGES):
            try:
                resp = yt.search().list(q=f"{plain} korean skincare", part="id", type="video", maxResults=50,
                                        publishedAfter=after, relevanceLanguage="en", regionCode="US",
                                        pageToken=token).execute()
            except Exception as e:
                logger.warning(f"2차 YouTube 검색 실패 {kw}: {e}")
                break
            ids += [it["id"]["videoId"] for it in resp.get("items", []) if it.get("id", {}).get("videoId")]
            token = resp.get("nextPageToken")
            if not token:
                break
        out = []
        for v in yt_tool._fetch_video_stats(ids):
            text = f"{v['title']} {v['description']}".strip()
            tags = v.get("tags", [])
            hay = f"{text} {' '.join(tags)}".lower()
            if plain not in hay and kw not in hay:
                continue
            out.append({"platform_id": "youtube", "post_id": v["video_id"], "content_text": text,
                        "hashtags": json.dumps(tags), "view_count": v["views"], "like_count": v["likes"]})
        return out

    # ── TikTok ──
    def _tiktok(self, kw: str) -> list[dict]:
        if not self.apify_token or self.TT_ITEMS <= 0:
            return []
        from tools.collect.tiktok_tool import TikTokTool
        tt = TikTokTool(self.db_path, self.apify_token)
        tag = kw.replace("_", "").lower()
        plain = kw.replace("_", " ")
        cutoff = tt._published_after_cutoff()
        out = []
        for v in tt._fetch_from_apify(tag, max_items=self.TT_ITEMS, published_after=tt._published_after_date()):
            if not tt._is_recent_post(v, cutoff):
                continue
            if v.get("textLanguage") and v.get("textLanguage") != "en":
                continue
            caption = v.get("text", "") or ""
            tags = [t.get("name", "") for t in v.get("hashtags", [])]
            hay = f"{caption} {' '.join(tags)}".lower()
            if plain not in hay and tag not in hay:
                continue
            pid = v.get("id", "") or v.get("webVideoUrl", "")
            if caption and pid:
                out.append({"platform_id": "tiktok", "post_id": str(pid), "content_text": caption,
                            "hashtags": json.dumps(tags), "view_count": int(v.get("playCount", 0) or 0),
                            "like_count": int(v.get("diggCount", 0) or 0)})
        return out

    def collect(self, keywords: list[str], week: str) -> dict:
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            conn.execute(SCHEMA_SQL)
        summary = {}
        for kw in keywords:
            rows = self._youtube(kw) + self._tiktok(kw)
            with sqlite3.connect(self.db_path, timeout=10) as conn:
                conn.executemany(
                    """INSERT OR IGNORE INTO fact_sns_deep
                       (week, keyword, platform_id, post_id, content_text, hashtags, view_count, like_count)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    [(week, kw, r["platform_id"], r["post_id"], r["content_text"], r["hashtags"],
                      r["view_count"], r["like_count"]) for r in rows])
            yt = sum(1 for r in rows if r["platform_id"] == "youtube")
            summary[kw] = {"youtube": yt, "tiktok": len(rows) - yt}
            logger.info(f"  2차 수집 [{kw}]: YouTube {yt} · TikTok {len(rows) - yt}")
        return summary
