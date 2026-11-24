"""
voc_tool.py
────────────
LLM 기반 VOC (Voice of Customer) 분석 툴.

수행 기능:
  1) 리뷰 텍스트 배치 분석 (최대 20개 리뷰 → 1개 API 호출)
  2) 감성 분석: sentiment_pos / sentiment_neg 비율
  3) 키워드 추출:
     - pos_keywords:   긍정 키워드 (ex. "lightweight", "glass skin")
     - neg_keywords:   부정 키워드 (ex. "too heavy", "broke me out")
     - needs_keywords: 소비자 니즈 (ex. "fragrance free", "more SPF")
  4) fact_voc_signals 저장

모델: VOC_MODEL 환경변수 (기본 claude-haiku-5-5, 비용 최적화)
"""

import json
import os
import sqlite3
import logging
from datetime import datetime, timezone

import anthropic

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
- Write every keyword in English, translating if the reviews are in another language
"""

DEFAULT_VOC_MODEL = "claude-haiku-5-5"

# 구조화 출력(JSON schema) — 응답을 항상 같은 필드로 받는다
VOC_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "sentiment_pos":       {"type": "number"},
        "sentiment_neg":       {"type": "number"},
        "pos_keywords":        {"type": "array", "items": {"type": "string"}},
        "neg_keywords":        {"type": "array", "items": {"type": "string"}},
        "needs_keywords":      {"type": "array", "items": {"type": "string"}},
        "avg_rating_estimate": {"type": "number", "description": "추정 불가하면 생략"},
    },
    "required": ["sentiment_pos", "sentiment_neg", "pos_keywords", "neg_keywords", "needs_keywords"],
}


class VOCTool:
    """LLM 기반 리뷰 감성 분석 + 키워드 추출."""

    def __init__(self, db_path: str, model: str | None = None):
        self.db_path = db_path
        self.model   = model or os.getenv("VOC_MODEL", DEFAULT_VOC_MODEL)
        self.client  = anthropic.Anthropic()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _current_week(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%G-W%V")

    # ──────────────────────────────────────────
    # LLM 분석 (배치: 최대 20개 리뷰)
    # ──────────────────────────────────────────
    def _analyze_reviews(self, reviews: list[str]) -> dict:
        """
        리뷰 목록 → LLM 분석 → 감성/키워드 dict 반환.
        실패 시 기본값 반환.
        """
        if not reviews:
            return {}

        # 최대 20개, 각 300자 제한
        review_text = "\n---\n".join(r[:300] for r in reviews[:20])

        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=SYSTEM_PROMPT,
                output_config={
                    "effort": "low",
                    "format": {"type": "json_schema", "schema": VOC_SCHEMA},
                },
                messages=[{"role": "user", "content": f"Reviews:\n{review_text}"}],
            )
            text = "".join(b.text for b in resp.content if b.type == "text")
            if not text:
                raise ValueError(f"응답 없음 (stop_reason={resp.stop_reason})")
            return json.loads(text)
        except Exception as e:
            logger.warning(f"VOC 분석 실패: {e}")
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
