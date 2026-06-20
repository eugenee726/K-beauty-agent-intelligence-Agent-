"""
analysis_agent.py
──────────────────
통계 분석 에이전트.

책임:
  - stats_tool: Z-score + t-test 이상 탐지, TikTok↔YouTube 상관 분석
  - momentum_tool: 크로스플랫폼 모멘텀 추적 (최근 3주 추세)
  - llm_insight_tool: Claude 기반 키워드 인사이트 추출
  - 분석 결과를 dict로 반환 → Decision Agent로 전달

분석 결과 구조:
  {
    "week":           str,
    "sns_anomalies":  DataFrame (keyword별 TikTok+YouTube 합산, z_score, is_anomaly...),
    "momentum":       DataFrame (keyword, momentum_score, opportunity_hint...),
    "brand_momentum": DataFrame (brand_id, keyword, engagement_score...),
    "cross_corr":     DataFrame (keyword, tiktok_youtube_corr...),
    "llm_insights":   list[dict],  -- LLM 인사이트 추출 결과
  }
"""

import logging

import pandas as pd

from memory.agent_memory import AgentMemory
from tools.analyze.stats_tool       import StatsTool
from tools.analyze.momentum_tool    import MomentumTool
from tools.analyze.llm_insight_tool import LLMInsightTool

logger = logging.getLogger(__name__)

# LLM·Decision과 동일한 볼륨 하한
MIN_POST_COUNT = StatsTool.MIN_POST_COUNT

# LLM 후보에서 제외 (광의어·경쟁사·AP 브랜드 — brand_momentum에서 별도 추적)
EXCLUDE_KEYWORDS = {
    "kbeauty", "korean_beauty", "korean_skincare", "skincare_routine", "k-beauty",
    "k_beauty", "routine", "morning_routine", "night_routine",
    "anua", "torriden", "beauty_of_joseon", "skin1004", "round_lab",
    "medicube", "mixsoon", "goodal", "isntree", "manyo", "iunik",
    "purito", "abib", "haruharu", "axis_y", "cosrx_rival",
    "cerave", "la_roche_posay", "bioderma", "avene", "neutrogena",
    "sulwhasoo", "hera", "primera", "tata_harper", "tata harper",
    "iope", "aestura", "mamonde", "hanyul", "laneige",
    "innisfree", "cosrx", "espoir", "etude", "etude house",
}


class AnalysisAgent:
    """통계 분석 + 모멘텀 추적 에이전트."""

    def __init__(self, memory: AgentMemory, week: str | None = None):
        self.memory = memory
        db = memory.db_path
        self.week = week if week is not None else memory.get_current_week()

        self.stats    = StatsTool(db)
        self.momentum = MomentumTool(db)
        self.llm      = LLMInsightTool(db)

    def _select_llm_candidates(
        self, candidates: pd.DataFrame, min_raw_samples: int = 3
    ) -> pd.DataFrame:
        """
        LLM 분석 대상 키워드 선정 (키워드 grain — raw는 플랫폼 통합):
          1) post_count 내림차순 → 2) z_score 내림차순
          3) raw 캡션 샘플(TikTok+YouTube) ≥ min_raw_samples
          4) 상위 10개
        """
        if candidates.empty:
            return candidates

        if "post_count" in candidates.columns and "z_score" in candidates.columns:
            candidates = candidates.sort_values(
                ["post_count", "z_score"],
                ascending=[False, False],
                na_position="last",
            )
        elif "z_score" in candidates.columns:
            candidates = candidates.sort_values(
                "z_score", ascending=False, na_position="last"
            )
        else:
            candidates = candidates.sort_values(
                "post_count", ascending=False, na_position="last"
            )

        keep = []
        for _, row in candidates.iterrows():
            kw = row["keyword"]
            raw_n = self.llm.count_sns_samples(kw, self.week)
            if raw_n >= min_raw_samples:
                keep.append(True)
            else:
                keep.append(False)
                logger.info(
                    f"  raw 샘플 부족 제외: {kw} "
                    f"(raw={raw_n} < {min_raw_samples})"
                )
        candidates = candidates[keep]

        return candidates.head(10)

    def run(self) -> dict:
        """
        Orchestrator 호출 진입점.
        반환: 분석 결과 dict
        """
        logger.info(f"=== Analysis Agent 시작 (week={self.week}) ===")

        # 1. 첫 등장 기록 업데이트
        try:
            self.momentum.update_first_seen(self.week)
            logger.info("첫 등장 기록 업데이트 완료")
        except Exception as e:
            logger.warning(f"첫 등장 기록 실패: {e}")

        # 2. SNS Z-score 이상 탐지 (TikTok+YouTube post_count·engagement 합산 후 키워드별)
        sns_anomalies = pd.DataFrame()
        try:
            sns_anomalies = self.stats.compute_sns_anomalies(self.week)
            anomaly_count = (
                int(sns_anomalies["is_anomaly"].sum()) if not sns_anomalies.empty else 0
            )
            logger.info(
                f"SNS 이상 탐지(크로스플랫폼): {len(sns_anomalies)}개 키워드, "
                f"이상치(z≥{StatsTool.Z_ALERT}) {anomaly_count}개"
            )
        except Exception as e:
            logger.warning(f"SNS 이상 탐지 실패: {e}")

        # 3. TikTok ↔ YouTube 상관 계수
        cross_corr = pd.DataFrame()
        try:
            cross_corr = self.stats.compute_cross_platform_correlation()
            logger.info(f"상관 분석: {len(cross_corr)}개 키워드")
        except Exception as e:
            logger.warning(f"상관 분석 실패: {e}")

        # 4. 모멘텀 분석
        momentum_df  = pd.DataFrame()
        brand_mom_df = pd.DataFrame()
        try:
            momentum_df  = self.momentum.compute_momentum(self.week)
            brand_mom_df = self.momentum.compute_brand_momentum(self.week)
            logger.info(
                f"모멘텀: {len(momentum_df)}개 키워드, "
                f"브랜드 키워드 {len(brand_mom_df)}개"
            )
        except Exception as e:
            logger.warning(f"모멘텀 분석 실패: {e}")

        # 5. LLM 인사이트 (is_anomaly + post_count 볼륨 검증)
        llm_insights = []
        try:
            if not sns_anomalies.empty and "is_anomaly" in sns_anomalies.columns:
                n_anomaly_rows = int((sns_anomalies["is_anomaly"] == 1).sum())

                candidates = sns_anomalies[sns_anomalies["is_anomaly"] == 1]
                llm_pool_source = "anomaly"

                if candidates.empty:
                    if (
                        "post_count" in sns_anomalies.columns
                        and "z_score" in sns_anomalies.columns
                    ):
                        candidates = sns_anomalies.sort_values(
                            ["post_count", "z_score"],
                            ascending=[False, False],
                            na_position="last",
                        )
                        llm_pool_source = "fallback:post_count,z_score"
                        fb_log = "post_count→z_score 정렬"
                    else:
                        score_col = (
                            "engagement_score"
                            if "engagement_score" in sns_anomalies.columns
                            else "z_score"
                        )
                        candidates = sns_anomalies.nlargest(
                            len(sns_anomalies), score_col
                        )
                        llm_pool_source = f"fallback:{score_col}"
                        fb_log = f"'{score_col}' 상위"
                    logger.info(
                        f"LLM fallback: is_anomaly 후보 없음 → "
                        f"{fb_log} 전체 사용 (n={len(candidates)})"
                    )

                n_before_exclude = len(candidates)
                logger.info(
                    f"  LLM 후보 풀 출처: {llm_pool_source}, "
                    f"후보 {n_before_exclude}개 (EXCLUDE 전) | "
                    f"참고: is_anomaly {n_anomaly_rows}개"
                )

                candidates = candidates[
                    ~candidates["keyword"].str.lower().isin(EXCLUDE_KEYWORDS)
                ]
                logger.info(
                    f"  LLM 후보 풀: EXCLUDE 후 {len(candidates)}개 "
                    f"(제외 {n_before_exclude - len(candidates)}개)"
                )

                if "post_count" in candidates.columns:
                    candidates = candidates[
                        candidates["post_count"] >= MIN_POST_COUNT
                    ]
                    if not candidates.empty:
                        candidates["trend_tier"] = candidates["post_count"].apply(
                            lambda x: "confirmed" if x >= 10 else "emerging"
                        )
                        confirmed = int(
                            (candidates["trend_tier"] == "confirmed").sum()
                        )
                        emerging = int(
                            (candidates["trend_tier"] == "emerging").sum()
                        )
                        logger.info(
                            f"  post_count 필터 적용 (≥{MIN_POST_COUNT}): "
                            f"confirmed={confirmed}개, emerging={emerging}개"
                        )
                    else:
                        logger.info(
                            f"  post_count 필터 적용 (≥{MIN_POST_COUNT}): "
                            f"confirmed=0개, emerging=0개"
                        )

                candidates = self._select_llm_candidates(
                    candidates, min_raw_samples=3
                )
                logger.info(f"  LLM 최종 선정: {len(candidates)}개 키워드")

                llm_keyword_list = [
                    {"keyword": row["keyword"], "z_score": float(row["z_score"])}
                    for _, row in candidates.iterrows()
                ]

                if llm_keyword_list:
                    llm_insights = self.llm.analyze(llm_keyword_list, self.week)
                    logger.info(f"LLM 인사이트: {len(llm_insights)}개 키워드 완료")
        except Exception as e:
            logger.warning(f"LLM 인사이트 분석 실패: {e}")

        result = {
            "week":           self.week,
            "sns_anomalies":  sns_anomalies,
            "momentum":       momentum_df,
            "brand_momentum": brand_mom_df,
            "cross_corr":     cross_corr,
            "llm_insights":   llm_insights,
        }

        logger.info("=== Analysis 완료 ===")
        return result
