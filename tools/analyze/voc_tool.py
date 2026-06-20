"""
voc_tool.py
────────────
GPT 기반 VOC (Voice of Customer) 분석 툴.

수행 기능:
  1) 리뷰 텍스트 배치 분석 (최대 20개 리뷰 → 1개 API 호출)
  2) 감성 분석: sentiment_pos / sentiment_neg 비율
  3) 키워드 추출:
     - pos_keywords:   긍정 키워드 (ex. "lightweight", "glass skin")
     - neg_keywords:   부정 키워드 (ex. "too heavy", "broke me out")
     - needs_keywords: 소비자 니즈 (ex. "fragrance free", "more SPF")
  4) fact_voc_signals 저장

GPT 모델: gpt-4o-mini (비용 최적화)
"""

import json
import sqlite3
import logging
from datetime import datetime, timezone

from openai import OpenAI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """
You are a K-beauty consumer insights analyst.
Analyze the provided product reviews and return a JSON object.

Return ONLY valid JSON with this exact structure:
{
  "sentiment_pos": <float 0.0-1.0>,
  "sentiment_neg": <float 0.0-1.0>,
  "pos_keywords": [<list of 3-8 positive keywords/phrases>],
  "neg_keywords": [<list of 3-8 negative keywords/phrases>],
  "needs_keywords": [<list of 3-6 consumer need phrases>],
  "avg_rating_estimate": <float 1.0-5.0 if inferable, else null>
}

Rules:
- pos_keywords: specific skin benefits, textures, results observed
- neg_keywords: specific complaints, side effects, disappointments
- needs_keywords: wishes, requests for improvement, "would like", "wish it had"
- sentiment_pos + sentiment_neg should roughly sum to 1.0
"""


class VOCTool:
    """GPT 기반 리뷰 감성 분석 + 키워드 추출."""

    def __init__(self, db_path: str, openai_api_key: str):
        self.db_path = db_path
        self.client  = OpenAI(api_key=openai_api_key)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _current_week(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%G-W%V")

    # ──────────────────────────────────────────
    # GPT 분석 (배치: 최대 20개 리뷰)
    # ──────────────────────────────────────────
    def _analyze_reviews(self, reviews: list[str]) -> dict:
        """
        리뷰 목록 → GPT 분석 → 감성/키워드 dict 반환.
        실패 시 기본값 반환.
        """
        if not reviews:
            return {}

        # 최대 20개, 각 300자 제한
        review_text = "\n---\n".join(r[:300] for r in reviews[:20])

        try:
            resp = self.client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": f"Reviews:\n{review_text}"},
                ],
                temperature=0.1,
                max_tokens=400,
                response_format={"type": "json_object"},
            )
            return json.loads(resp.choices[0].message.content)
        except Exception as e:
            logger.warning(f"GPT VOC 분석 실패: {e}")
            return {}

    # ──────────────────────────────────────────
    # fact_voc_signals 저장
    # ──────────────────────────────────────────
    def analyze_and_store(
        self,
        platform_id: str,
        product_id: str,
        reviews: list[str],
        avg_rating: float = None,
        review_count: int = None,
        week: str = None,
        total_reviews: int = None,
        platform_avg_rating: float = None,
    ) -> bool:
        """
        리뷰 분석 후 fact_voc_signals에 저장.
        Collection Agent가 리테일 툴로부터 받은 voc_pending을 처리할 때 호출.

        total_reviews / platform_avg_rating: 플랫폼 전체 통계 (velocity·별점 추세용).
        """
        week = week or self._current_week()

        if not product_id:
            logger.warning("VOC: product_id 없음, 스킵")
            return False

        result = self._analyze_reviews(reviews)
        if not result:
            return False

        rc = review_count or len(reviews)

        try:
            with self._conn() as conn:
                # 기존 DB에 신규 컬럼 없으면 추가
                cols = [r[1] for r in conn.execute("PRAGMA table_info(fact_voc_signals)").fetchall()]
                if "total_reviews" not in cols:
                    conn.execute("ALTER TABLE fact_voc_signals ADD COLUMN total_reviews INTEGER")
                if "platform_avg_rating" not in cols:
                    conn.execute("ALTER TABLE fact_voc_signals ADD COLUMN platform_avg_rating REAL")
                conn.execute("""
                    INSERT OR REPLACE INTO fact_voc_signals
                        (week, platform_id, product_id,
                         review_count, avg_rating, total_reviews, platform_avg_rating,
                         sentiment_pos, sentiment_neg,
                         pos_keywords, neg_keywords, needs_keywords)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    week, platform_id, product_id,
                    rc,
                    avg_rating or result.get("avg_rating_estimate"),
                    total_reviews,
                    platform_avg_rating,
                    result.get("sentiment_pos"),
                    result.get("sentiment_neg"),
                    json.dumps(result.get("pos_keywords", [])),
                    json.dumps(result.get("neg_keywords", [])),
                    json.dumps(result.get("needs_keywords", [])),
                ))
            logger.info(
                f"VOC 저장: {platform_id}/{product_id} "
                f"(pos={result.get('sentiment_pos'):.2f}, "
                f"neg={result.get('sentiment_neg'):.2f})"
            )
            return True
        except Exception as e:
            logger.error(f"VOC DB 저장 실패: {e}")
            return False

    # ──────────────────────────────────────────
    # 배치 처리 (리테일 툴 voc_pending 일괄 처리)
    # ──────────────────────────────────────────
    def process_voc_batch(
        self,
        platform_id: str,
        voc_pending: list[dict],
        week: str = None,
    ) -> int:
        """
        voc_pending = [{"product_id": "...", "reviews": [...], "avg_rating": ...}]
        반환: 저장된 레코드 수
        """
        week    = week or self._current_week()
        saved   = 0
        for item in voc_pending:
            ok = self.analyze_and_store(
                platform_id = platform_id,
                product_id  = item.get("product_id"),
                reviews     = item.get("reviews", []),
                avg_rating  = item.get("avg_rating"),
                week        = week,
            )
            if ok:
                saved += 1
        logger.info(f"VOC 배치 완료: {saved}/{len(voc_pending)}건 저장")
        return saved

    # ──────────────────────────────────────────
    # VOC 인사이트 조회 (에이전트 참조용)
    # ──────────────────────────────────────────
    def get_voc_summary(self, week: str = None, brand_id: str = None) -> list[dict]:
        """
        저장된 VOC 데이터 요약 조회.
        brand_id 지정 시 해당 브랜드 제품만 반환.
        """
        week = week or self._current_week()

        query = """
            SELECT v.week, v.platform_id, v.product_id,
                   p.product_name_en, p.brand_id,
                   v.review_count, v.avg_rating,
                   v.sentiment_pos, v.sentiment_neg,
                   v.pos_keywords, v.neg_keywords, v.needs_keywords
            FROM fact_voc_signals v
            LEFT JOIN dim_product p ON v.product_id = p.product_id
            WHERE v.week = ?
        """
        params = [week]

        if brand_id:
            query += " AND p.brand_id = ?"
            params.append(brand_id)

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()

        results = []
        for r in rows:
            results.append({
                "week":          r["week"],
                "platform_id":   r["platform_id"],
                "product_id":    r["product_id"],
                "product_name":  r["product_name_en"],
                "brand_id":      r["brand_id"],
                "review_count":  r["review_count"],
                "avg_rating":    r["avg_rating"],
                "sentiment_pos": r["sentiment_pos"],
                "sentiment_neg": r["sentiment_neg"],
                "pos_keywords":  json.loads(r["pos_keywords"] or "[]"),
                "neg_keywords":  json.loads(r["neg_keywords"] or "[]"),
                "needs_keywords": json.loads(r["needs_keywords"] or "[]"),
            })
        return results
