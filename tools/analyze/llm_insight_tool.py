"""
llm_insight_tool.py
────────────────────
LLM 기반 SNS 인사이트 추출 툴.

역할:
  통계 분석(StatsTool)이 탐지한 급상승 키워드에 대해:
  1. fact_sns_raw에서 해당 키워드 관련 캡션 샘플링
  2. 키워드와 관련된 후보 제품만 카탈로그에서 추림 (추적 브랜드 + 이번 주 랭킹 제품)
  3. Claude API 호출 → 제품 연결 / 브랜드 언급 / 소비자 니즈 추출 (특정 회사 관점 없음)
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

from tools.collect.brand_registry import ensure_brand_schema

logger = logging.getLogger(__name__)

# 키워드당 샘플링 수
MAX_SNS_SAMPLES = 30
# 프롬프트에 넣을 후보 제품 수 상한 (전체 카탈로그 2,000~3,000개를 넣으면 TPM 한도 초과)
MAX_CANDIDATES = 150
# 요청 간 대기 (후보 추리기로 요청당 입력이 ~10K 토큰 수준이 되어 65초 → 30초)
REQUEST_GAP_SEC = 30


def sample_captions(conn: sqlite3.Connection, keyword: str, week: str,
                    limit: int = MAX_SNS_SAMPLES) -> list[str]:
    """
    LLM 에 넣는 캡션 샘플 (키워드 포함, 조회수 상위).
    검증 단계(tools/quality/insight_checks.py)도 LLM 이 본 것과 같은 샘플로 대조하려고 공유한다.
    """
    kw_plain = keyword.replace("_", " ").lower()
    low = keyword.lower()
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
    """, (week, f"%{low}%", f"%{kw_plain}%", f"%{low}%", limit)).fetchall()
    return [r[0] for r in rows]


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
    """급상승 키워드 → Claude API → 제품/브랜드 언급/소비자 니즈 추출."""

    def __init__(self, db_path: str, model: str = None):
        self.db_path  = db_path
        self.model    = model or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")
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
        with self._conn() as conn:
            return sample_captions(conn, keyword, week)

    def _get_candidate_catalog(self, keyword: str, week: str) -> list[dict]:
        """
        키워드와 관련된 후보 제품만 반환 (최대 MAX_CANDIDATES).

        대상: 추적 브랜드 제품 + 이번 주 랭킹에 오른 제품(브랜드 무관).
        우선순위: ① 키워드 토큰이 제품명·성분·효능·카테고리에 모두 포함
                  ② 일부 포함  ③ 이번 주 랭킹 상위 (부족분 채움)
        """
        tokens = [t for t in re.split(r"[_\s\-]+", keyword.lower()) if len(t) > 1]
        with self._conn() as conn:
            ensure_brand_schema(conn)
            rows = conn.execute("""
                SELECT p.product_id, p.brand_id, p.product_name_en,
                       p.category_main, p.key_ingredients, p.key_benefits,
                       MIN(r.rank_position) AS best_rank
                FROM dim_product p
                JOIN dim_brand b ON b.brand_id = p.brand_id
                LEFT JOIN fact_retail_rankings r
                       ON r.product_id = p.product_id AND r.week = ?
                WHERE b.is_tracked = 1 OR r.product_id IS NOT NULL
                GROUP BY p.product_id
            """, (week,)).fetchall()

        def score(p) -> int:
            text = " ".join(str(p[k] or "") for k in
                            ("product_name_en", "category_main", "key_ingredients", "key_benefits")).lower()
            hits = sum(1 for t in tokens if t in text)
            return 2 if tokens and hits == len(tokens) else (1 if hits else 0)

        scored = [(score(r), r["best_rank"] if r["best_rank"] is not None else 10_000, dict(r))
                  for r in rows]
        matched = sorted((x for x in scored if x[0] > 0), key=lambda x: (-x[0], x[1]))
        ranked = sorted((x for x in scored if x[0] == 0 and x[1] < 10_000), key=lambda x: x[1])
        picked = [x[2] for x in (matched + ranked)[:MAX_CANDIDATES]]
        logger.info(f"  [{keyword}] 후보 제품 {len(picked)}개 (키워드 매칭 {len(matched)} / 전체 {len(rows)})")
        return picked

    def _get_valid_product_ids(self) -> set:
        """dim_product에 실제 존재하는 product_id 집합 반환 (hallucination 방지)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT product_id FROM dim_product").fetchall()
        return {r["product_id"] for r in rows}

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
        catalog: list[dict],
        oy_rankings: list[dict],
    ) -> str:
        # 압축 포맷: [pid][brand] ProductName|ingredients|benefits (제품당 ~35토큰)
        def _compact_catalog_line(p: dict) -> str:
            ingr = (p.get("key_ingredients") or "").replace('"', '').replace('[', '').replace(']', '')
            bene = (p.get("key_benefits") or "").replace('"', '').replace('[', '').replace(']', '')
            return (
                f"[{p['product_id']}][{p['brand_id']}] {p['product_name_en']}"
                f"|{ingr[:50]}|{bene[:50]}"
            )

        catalog_lines = [_compact_catalog_line(p) for p in catalog]
        sns_lines = [f"{i+1}. {t[:200]}" for i, t in enumerate(sns_samples)]
        ranking_lines = [
            f"- {r['platform_id']} #{r['rank_position']} "
            f"[{r['product_id']}] [{r['brand_id']}] {r['product_name_en']}"
            for r in oy_rankings[:15]
        ]
        z_context = (
            f"z-score: {z_score:.1f} (statistically anomalous this week)"
            if z_score >= 2.0
            else f"z-score: {z_score:.1f} (high engagement this week)"
        )

        return f"""You are a K-beauty market trend analyst covering the US market.
You do not represent any company: treat every brand neutrally.

Analyze the keyword "{keyword}" ({z_context}).

## SNS Raw Captions ({len(sns_samples)} samples)
{chr(10).join(sns_lines) if sns_lines else "No raw caption data available this week."}

## Candidate Product Catalog (pre-filtered for this keyword; format: [product_id] [brand] name | ingredients | benefits)
{chr(10).join(catalog_lines) if catalog_lines else "No candidate products."}

## OY Global Rankings (this week, format: platform #rank [product_id] [brand] name)
{chr(10).join(ranking_lines) if ranking_lines else "No ranking data available."}

---
Based on the above data, provide a JSON response with this exact structure:
{{
  "products_direct": [
    {{"product_id": "S00001", "product_name": "...", "evidence": "exact quote from caption showing brand/product mention (under 80 chars)"}}
  ],
  "products_indirect": [
    {{"product_id": "S00002", "product_name": "...", "evidence": "which ingredient/benefit/category connects this product to the trend (under 80 chars)"}}
  ],
  "brand_mentions": {{"brand_name": mention_count}},
  "consumer_need": "소비자가 실제로 원하는 바를 1~2문장으로 요약 (반드시 한국어로 작성)",
  "consumer_language": "key phrases consumers use (comma separated)",
  "opportunity": "1-2 sentence market opportunity or white space this trend creates for K-beauty brands",
  "confidence": 0.0
}}

Rules:
- products_direct: ONLY include if the brand name or product name visibly appears in the SNS caption text. Evidence MUST be a direct quote (under 80 chars) from the caption. Max 3 products.
- products_indirect: products whose key_ingredients or key_benefits directly address the keyword trend. Evidence MUST state the ingredient or benefit connection (e.g. "contains ceramide NP which strengthens skin barrier"). Max 5 products. Prefer variety across brands when the evidence is equally strong.
- product_id MUST be copied exactly from the catalog. Do NOT invent or guess IDs.
- Both direct and indirect MUST always include a non-empty evidence string. Empty evidence = do not include the product.
- brand_mentions: every brand (K-beauty or not) explicitly named in the captions, with how many captions mention it.
- consumer_language: use actual phrases/hashtags found in the captions, not generic descriptions.
- consumer_need: MUST be written in Korean (한국어). All other fields stay in English.
- confidence: 0.0~1.0 reflecting how strongly the SNS data supports your analysis.
- Respond with JSON only, no additional text.
"""

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
                # 컬럼명은 호환을 위해 유지 — 의미는 "캡션에 언급된 전체 브랜드"
                json.dumps(result.get("brand_mentions", result.get("competitor_mentions", {}))),
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
            for p in result.get("products_direct", result.get("ap_products_direct", [])):
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
            for p in result.get("products_indirect", result.get("ap_products_indirect", [])):
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

        oy_rankings = self._get_oy_rankings(week)

        import time

        results = []
        for idx, item in enumerate(trend_keywords):
            keyword = item["keyword"]
            z_score = item.get("z_score", 0.0)

            # Rate limit 방지: 후보 추리기로 요청당 입력 ~10K 토큰 → 30초 간격이면 30K TPM 안쪽
            if idx > 0:
                time.sleep(REQUEST_GAP_SEC)

            sns_samples = self._get_sns_samples(keyword, week)
            raw_count   = len(sns_samples)

            logger.info(f"  [{keyword}] SNS {raw_count}건 / z={z_score:.2f}")

            catalog = self._get_candidate_catalog(keyword, week)
            prompt = self._build_prompt(keyword, z_score, sns_samples, catalog, oy_rankings)

            llm_result = self._call_llm(prompt)
            if not llm_result:
                logger.warning(f"  [{keyword}] LLM 응답 없음, 스킵")
                continue

            llm_result["keyword"]   = keyword
            llm_result["z_score"]   = z_score
            llm_result["raw_count"] = raw_count

            self._save_insight(week, keyword, llm_result, raw_count)
            results.append(llm_result)

            direct_count   = len([p for p in llm_result.get("products_direct", [])
                                   if isinstance(p, dict)])
            indirect_count = len([p for p in llm_result.get("products_indirect", [])
                                   if isinstance(p, dict)])
            logger.info(
                f"  [{keyword}] 완료 — "
                f"직접={direct_count}개 / 간접={indirect_count}개 / "
                f"신뢰도={llm_result.get('confidence', 0):.2f}"
            )

        logger.info(f"LLM 인사이트 완료: {len(results)}/{len(trend_keywords)}개")
        return results
