"""
llm_insight_tool.py
────────────────────
LLM 기반 SNS 인사이트 추출 툴.

역할:
  통계 분석(StatsTool)이 탐지한 급상승 키워드에 대해:
  1. fact_sns_raw에서 해당 키워드 관련 캡션 샘플링
  2. dim_product에서 AP 제품 카탈로그 조회 (product_id 포함)
  3. Claude API 호출 → AP 제품 연결 / 경쟁사 탐지 / 소비자 니즈 추출
  4. fact_llm_insights + fact_llm_insight_products 저장

출력:
  list[dict] — 키워드별 LLM 인사이트
"""

import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone

import anthropic
from pathlib import Path

logger = logging.getLogger(__name__)

# 키워드당 샘플링 수
MAX_SNS_SAMPLES = 30


def _load_env_utf8() -> None:
    """Windows cp949 환경에서 .env UTF-8 파싱 보조."""
    root = Path(__file__).parent.parent.parent
    env_path = root / ".env"
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                v = v.split(" #")[0].strip()
                os.environ[k.strip()] = v


_load_env_utf8()


class LLMInsightTool:
    """급상승 키워드 → Claude API → 제품/경쟁사/소비자 니즈 추출."""

    def __init__(self, db_path: str, model: str = None):
        self.db_path  = db_path
        self.model    = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5-20251001")
        api_key       = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY 환경변수가 설정되지 않았습니다.")
        self.client   = anthropic.Anthropic(api_key=api_key)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _current_week(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%G-W%V")

    # ──────────────────────────────────────────
    # 데이터 조회
    # ──────────────────────────────────────────
    def _keyword_like_params(self, keyword: str) -> tuple[str, str, str]:
        kw_plain = keyword.replace("_", " ")
        low = keyword.lower()
        return f"%{low}%", f"%{kw_plain.lower()}%", f"%{low}%"

    def count_sns_samples(self, keyword: str, week: str) -> int:
        """fact_sns_raw에서 LLM용 캡션 건수 (TikTok+YouTube 통합, 플랫폼 무관)."""
        p1, p2, p3 = self._keyword_like_params(keyword)
        with self._conn() as conn:
            row = conn.execute("""
                SELECT COUNT(*)
                FROM fact_sns_raw
                WHERE week = ?
                  AND TRIM(COALESCE(content_text, '')) != ''
                  AND (
                      LOWER(content_text) LIKE ?
                      OR LOWER(content_text) LIKE ?
                      OR LOWER(hashtags)     LIKE ?
                  )
            """, (week, p1, p2, p3)).fetchone()
        return int(row[0]) if row else 0

    def _get_sns_samples(self, keyword: str, week: str) -> list[str]:
        """keyword가 포함된 raw 캡션 샘플 (TikTok+YouTube 통합, 조회수 상위)."""
        p1, p2, p3 = self._keyword_like_params(keyword)
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT content_text
                FROM fact_sns_raw
                WHERE week = ?
                  AND TRIM(COALESCE(content_text, '')) != ''
                  AND (
                      LOWER(content_text) LIKE ?
                      OR LOWER(content_text) LIKE ?
                      OR LOWER(hashtags)     LIKE ?
                  )
                ORDER BY view_count DESC
                LIMIT ?
            """, (week, p1, p2, p3, MAX_SNS_SAMPLES)).fetchall()
        return [r["content_text"] for r in rows]

    def _get_ap_catalog(self) -> list[dict]:
        """AP 제품 카탈로그 조회 (product_id 포함)."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT p.product_id, p.brand_id, p.product_name_en,
                       p.category_main, p.key_ingredients, p.key_benefits
                FROM dim_product p
                ORDER BY p.brand_id, p.product_name_en
            """).fetchall()
        return [dict(r) for r in rows]

    def _get_valid_product_ids(self) -> set:
        """dim_product에 실제 존재하는 product_id 집합 반환 (hallucination 방지)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT product_id FROM dim_product").fetchall()
        return {r["product_id"] for r in rows}

    def _get_ap_brand_names(self) -> list[str]:
        """dim_brand에서 AP 브랜드 display name 목록 반환."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT brand_id, brand_name_en FROM dim_brand ORDER BY brand_id"
            ).fetchall()
        return [r["brand_name_en"] or r["brand_id"] for r in rows]

    def _get_oy_rankings(self, week: str) -> list[dict]:
        """OY Global Top Orders / Top in Korea 이번 주 순위 조회."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT r.platform_id, r.rank_position, r.category,
                       p.product_id, p.product_name_en, p.brand_id
                FROM fact_retail_rankings r
                JOIN dim_product p ON r.product_id = p.product_id
                WHERE r.week = ?
                ORDER BY r.platform_id, r.rank_position
                LIMIT 30
            """, (week,)).fetchall()
        return [dict(r) for r in rows]

    # ──────────────────────────────────────────
    # 프롬프트 구성
    # ──────────────────────────────────────────
    def _build_prompt(
        self,
        keyword: str,
        z_score: float,
        sns_samples: list[str],
        ap_catalog: list[dict],
        oy_rankings: list[dict],
        ap_brand_names: list[str] = None,
    ) -> str:
        # AP 카탈로그 포맷 — 전체 719개 유지, 압축 포맷으로 토큰 절감
        # 압축 포맷: [pid][brand] ProductName|ingr1,ingr2|benefit1,benefit2
        # 제품당 ~80자 → 719개 × 80자 / 4 ≈ 14,000 토큰 (기존 27,000 → 절반)
        def _compact_catalog_line(p: dict) -> str:
            ingr = (p.get("key_ingredients") or "").replace('"', '').replace('[', '').replace(']', '')
            bene = (p.get("key_benefits") or "").replace('"', '').replace('[', '').replace(']', '')
            # JSON 배열 형태면 쉼표 구분 텍스트로 변환, 각 40자 내로 절삭
            return (
                f"[{p['product_id']}][{p['brand_id']}] {p['product_name_en']}"
                f"|{ingr[:50]}|{bene[:50]}"
            )

        catalog_lines = [_compact_catalog_line(p) for p in ap_catalog]

        # SNS 캡션 샘플 포맷
        sns_lines = [f"{i+1}. {t[:200]}" for i, t in enumerate(sns_samples)]

        # OY 순위 포맷
        ranking_lines = []
        for r in oy_rankings[:15]:
            ranking_lines.append(
                f"- {r['platform_id']} #{r['rank_position']} "
                f"[{r['product_id']}] [{r['brand_id']}] {r['product_name_en']}"
            )

        z_context = (
            f"z-score: {z_score:.1f} (statistically anomalous this week)"
            if z_score >= 2.0
            else f"z-score: {z_score:.1f} (high engagement this week)"
        )

        # AP 브랜드 목록 포맷
        ap_brands_str = ", ".join(ap_brand_names) if ap_brand_names else ""

        prompt = f"""You are a K-beauty trend analyst for Amorepacific.

Analyze the keyword "{keyword}" ({z_context}).

## Amorepacific Brand Portfolio (these are ALL AP-owned brands — never list them as competitors)
{ap_brands_str}

## SNS Raw Captions ({len(sns_samples)} samples)
{chr(10).join(sns_lines) if sns_lines else "No raw caption data available this week."}

## Amorepacific Product Catalog (format: [product_id] [brand] name | ingredients | benefits)
{chr(10).join(catalog_lines)}

## OY Global Rankings (this week, format: platform #rank [product_id] [brand] name)
{chr(10).join(ranking_lines) if ranking_lines else "No ranking data available."}

---
Based on the above data, provide a JSON response with this exact structure:
{{
  "ap_products_direct": [
    {{"product_id": "S00001", "product_name": "...", "evidence": "exact quote from caption showing brand/product mention (under 80 chars)"}}
  ],
  "ap_products_indirect": [
    {{"product_id": "S00002", "product_name": "...", "evidence": "reason for indirect match — which ingredient/benefit/category connects to this trend (under 80 chars)"}}
  ],
  "competitor_mentions": {{"brand_name": mention_count}},
  "consumer_need": "소비자가 실제로 원하는 바를 1~2문장으로 요약 (반드시 한국어로 작성)",
  "consumer_language": "key phrases consumers use (comma separated)",
  "opportunity": "1-2 sentence strategic opportunity for Amorepacific",
  "confidence": 0.0
}}

Rules:
- ap_products_direct: ONLY include if the exact AP brand name or product name visibly appears in the SNS caption text. Evidence MUST be a direct quote (under 80 chars) from the caption. Max 3 products.
- ap_products_indirect: Include products whose key_ingredients or key_benefits directly address the keyword trend. Evidence MUST explicitly state the ingredient or benefit connection (e.g. "contains ceramide NP which directly strengthens skin barrier"). Max 5 products.
- product_id MUST be copied exactly from the catalog (e.g. "S00001234"). Do NOT invent or guess IDs.
- Both direct and indirect MUST always include a non-empty evidence string. Empty evidence = do not include the product.
- competitor_mentions: ONLY include brands NOT listed in the AP Brand Portfolio above. AP-owned brands must never appear here.
- consumer_language: use actual phrases/hashtags found in the captions, not generic descriptions.
- consumer_need: MUST be written in Korean (한국어). All other fields stay in English.
- confidence: 0.0~1.0 reflecting how strongly the SNS data supports your analysis.
- Respond with JSON only, no additional text.
"""
        return prompt

    # ──────────────────────────────────────────
    # Claude API 호출
    # ──────────────────────────────────────────
    def _call_llm(self, prompt: str, _retry: int = 0) -> dict:
        import time as _time
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()

            # JSON 블록 추출 (```json ... ``` 감싸인 경우 대응)
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                return json.loads(match.group())
            return json.loads(text)

        except json.JSONDecodeError as e:
            logger.warning(f"LLM JSON 파싱 실패: {e}")
            return {}
        except Exception as e:
            # 429 rate limit → 75초 대기 후 1회 재시도
            if "429" in str(e) and _retry == 0:
                logger.warning(f"Rate limit 감지 — 75초 대기 후 재시도...")
                _time.sleep(75)
                return self._call_llm(prompt, _retry=1)
            logger.error(f"Claude API 호출 실패: {e}")
            return {}

    # ──────────────────────────────────────────
    # DB 저장
    # ──────────────────────────────────────────
    def _save_insight(self, week: str, keyword: str, result: dict, raw_count: int) -> None:
        valid_ids = self._get_valid_product_ids()

        with self._conn() as conn:
            # 1. fact_llm_insights 요약 저장
            conn.execute("""
                INSERT OR REPLACE INTO fact_llm_insights
                    (week, keyword, competitor_mentions,
                     consumer_need, consumer_language,
                     opportunity, confidence, raw_post_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                week, keyword,
                json.dumps(result.get("competitor_mentions", {})),
                result.get("consumer_need", ""),
                result.get("consumer_language", ""),
                result.get("opportunity", ""),
                float(result.get("confidence", 0.0)),
                raw_count,
            ))

            # insight_id 조회
            insight_id = conn.execute(
                "SELECT id FROM fact_llm_insights WHERE week=? AND keyword=?",
                (week, keyword)
            ).fetchone()[0]

            # 기존 product rows 초기화 (재실행 시 중복 방지)
            conn.execute(
                "DELETE FROM fact_llm_insight_products WHERE insight_id=?",
                (insight_id,)
            )

            # 2. direct 제품 저장 (product_id 유효성 검증)
            for p in result.get("ap_products_direct", []):
                if not isinstance(p, dict):
                    continue
                pid = p.get("product_id", "")
                if pid not in valid_ids:
                    logger.debug(f"  유효하지 않은 product_id 스킵: {pid}")
                    continue
                conn.execute("""
                    INSERT OR IGNORE INTO fact_llm_insight_products
                        (insight_id, week, keyword, product_id, match_type, evidence)
                    VALUES (?, ?, ?, ?, 'direct', ?)
                """, (insight_id, week, keyword, pid, p.get("evidence", "")))

            # 3. indirect 제품 저장 (product_id 유효성 검증, evidence 포함)
            for p in result.get("ap_products_indirect", []):
                if not isinstance(p, dict):
                    continue
                pid = p.get("product_id", "")
                if pid not in valid_ids:
                    logger.debug(f"  유효하지 않은 product_id 스킵: {pid}")
                    continue
                evidence = p.get("evidence") or None
                conn.execute("""
                    INSERT OR IGNORE INTO fact_llm_insight_products
                        (insight_id, week, keyword, product_id, match_type, evidence)
                    VALUES (?, ?, ?, ?, 'indirect', ?)
                """, (insight_id, week, keyword, pid, evidence))

    # ──────────────────────────────────────────
    # 메인 인터페이스
    # ──────────────────────────────────────────
    def analyze(self, trend_keywords: list[dict], week: str = None) -> list[dict]:
        """
        AnalysisAgent 호출 인터페이스.

        Args:
            trend_keywords: [{"keyword": str, "z_score": float}, ...]
            week: 'YYYY-WNN' (None이면 현재 주)

        Returns:
            list[dict] — 키워드별 LLM 인사이트
        """
        week = week or self._current_week()
        logger.info(f"LLM 인사이트 분석 시작: {len(trend_keywords)}개 키워드 (week={week})")

        ap_catalog      = self._get_ap_catalog()
        oy_rankings     = self._get_oy_rankings(week)
        ap_brand_names  = self._get_ap_brand_names()

        import time

        results = []
        for idx, item in enumerate(trend_keywords):
            keyword = item["keyword"]
            z_score = item.get("z_score", 0.0)

            # Rate limit 방지: 첫 번째 요청 이후 매 요청 전 65초 대기
            # 요청당 ~28,000 tokens (전체 719개 카탈로그 유지, 압축 포맷)
            # Sonnet 30,000 TPM → 요청 1건이 분당 거의 전부 소진 → 60s+ 대기 필수
            if idx > 0:
                time.sleep(65)

            sns_samples = self._get_sns_samples(keyword, week)
            raw_count   = len(sns_samples)

            logger.info(f"  [{keyword}] SNS {raw_count}건 / z={z_score:.2f}")

            prompt = self._build_prompt(
                keyword, z_score, sns_samples,
                ap_catalog, oy_rankings,
                ap_brand_names=ap_brand_names,
            )

            llm_result = self._call_llm(prompt)
            if not llm_result:
                logger.warning(f"  [{keyword}] LLM 응답 없음, 스킵")
                continue

            llm_result["keyword"]   = keyword
            llm_result["z_score"]   = z_score
            llm_result["raw_count"] = raw_count

            self._save_insight(week, keyword, llm_result, raw_count)
            results.append(llm_result)

            direct_count   = len([p for p in llm_result.get("ap_products_direct", [])
                                   if isinstance(p, dict)])
            indirect_count = len([p for p in llm_result.get("ap_products_indirect", [])
                                   if isinstance(p, dict)])
            logger.info(
                f"  [{keyword}] 완료 — "
                f"직접={direct_count}개 / 간접={indirect_count}개 / "
                f"신뢰도={llm_result.get('confidence', 0):.2f}"
            )

        logger.info(f"LLM 인사이트 완료: {len(results)}/{len(trend_keywords)}개")
        return results
