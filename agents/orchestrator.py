"""
orchestrator.py
─────────────────
K-Beauty Intelligence Agent v3 — 최상위 조율자.

실행 순서:
  1. CollectionAgent  — 데이터 수집 (SNS / Google Trends / 리테일)
  2. AnalysisAgent    — 통계 분석 (이상 탐지 / 모멘텀 / 루틴)
  3. DecisionAgent    — 기회 분류 + 리포트 생성

각 에이전트 실패 시 다음 단계로 진행 (partial 성공 처리).
실행 이력은 mem_agent_runs에 기록.

정기 실행은 Apache Airflow DAG(airflow/dags/)가 담당한다. DAG 는 단계별 메서드
(run_collect / run_analyze / run_decide / build_catalog)와 get_pipeline_status 를 호출한다.
이전의 LLM 오케스트레이터(run_full_agentic)는 흐름이 고정돼 있어 제거했다.
"""

import logging
import sqlite3
import time
from pathlib import Path

from memory.agent_memory import AgentMemory
from agents.collection_agent import CollectionAgent
from agents.analysis_agent   import AnalysisAgent
from agents.decision_agent   import DecisionAgent

logger = logging.getLogger(__name__)


class Orchestrator:
    """전체 파이프라인 조율."""

    BASE_DIR    = Path(__file__).parent.parent
    DB_PATH     = BASE_DIR / "db" / "kbeauty.db"
    SCHEMA_PATH = BASE_DIR / "db" / "schema.sql"

    def __init__(
        self,
        db_path: str     = None,
        schema_path: str = None,
    ):
        self.db_path     = db_path     or str(self.DB_PATH)
        self.schema_path = schema_path or str(self.SCHEMA_PATH)
        self.memory      = AgentMemory(
            db_path     = self.db_path,
            schema_path = self.schema_path,
        )

    # ──────────────────────────────────────────
    # 전체 파이프라인
    # ──────────────────────────────────────────
    def run_full(self, run_type: str = "scheduled") -> dict:
        """
        수집 → 분석 → 의사결정 전체 파이프라인 실행.
        반환: 실행 결과 요약 dict
        """
        run_id    = self.memory.start_run(run_type)
        t_start   = time.time()
        status    = "success"
        error_msg = None
        stats: dict = {}

        try:
            # ── Step 1: 수집 ──────────────────────
            logger.info("=" * 50)
            logger.info("Step 1/3 — Collection Agent")
            logger.info("=" * 50)
            try:
                collect_result = CollectionAgent(self.memory).run()
                stats.update(collect_result)
                logger.info(f"수집 완료: {collect_result}")
            except Exception as e:
                logger.error(f"수집 단계 실패: {e}", exc_info=True)
                error_msg = f"collection: {e}"
                status    = "partial"
                collect_result = {}

            # ── Step 2: 분석 ──────────────────────
            logger.info("=" * 50)
            logger.info("Step 2/3 — Analysis Agent")
            logger.info("=" * 50)
            analysis_result = {}
            try:
                analysis_result = AnalysisAgent(self.memory).run()
                logger.info("분석 완료")
            except Exception as e:
                logger.error(f"분석 단계 실패: {e}", exc_info=True)
                if not error_msg:
                    error_msg = f"analysis: {e}"
                status = "partial"

            # ── Step 3: 의사결정 ──────────────────
            logger.info("=" * 50)
            logger.info("Step 3/3 — Decision Agent")
            logger.info("=" * 50)
            decision_result = {}
            try:
                decision_result = DecisionAgent(self.memory).run(analysis_result)
                stats["opportunities"]   = decision_result.get("opportunities", 0)
                stats["voc_records"]     = (
                    stats.get("voc_records", 0) + decision_result.get("voc_triggered", 0)
                )
                logger.info(
                    f"의사결정 완료: {decision_result.get('opportunities')}건, "
                    f"VOC {decision_result.get('voc_triggered', 0)}건"
                )
            except Exception as e:
                logger.error(f"의사결정 단계 실패: {e}", exc_info=True)
                if not error_msg:
                    error_msg = f"decision: {e}"
                status = "partial"

        except Exception as e:
            logger.critical(f"파이프라인 예외: {e}", exc_info=True)
            status    = "failed"
            error_msg = str(e)

        finally:
            duration = round(time.time() - t_start, 1)
            self.memory.finish_run(
                run_id   = run_id,
                status   = status,
                stats    = stats,
                error    = error_msg,
                duration = duration,
            )

        summary = {
            "run_id":    run_id,
            "status":    status,
            "duration":  duration,
            "week":      self.memory.get_current_week(),
            **stats,
            "report_path": decision_result.get("report_path"),
            "top_opps":    decision_result.get("top_opps", []),
        }

        logger.info("=" * 50)
        logger.info(f"파이프라인 완료 | status={status} | {duration}s")
        logger.info("=" * 50)
        return summary

    # ──────────────────────────────────────────
    # 단계별 실행
    # ──────────────────────────────────────────
    def run_collect(self, only: list[str] = None) -> dict:
        """수집만 실행. only: 특정 플랫폼만 실행 (None이면 전체)."""
        run_id  = self.memory.start_run("collect_only")
        t_start = time.time()
        try:
            result = CollectionAgent(self.memory).run(only=only)
            self.memory.finish_run(run_id, "success", result, duration=time.time()-t_start)
            return result
        except Exception as e:
            self.memory.finish_run(run_id, "failed", error=str(e), duration=time.time()-t_start)
            raise

    def run_analyze(self, week: str | None = None) -> dict:
        """
        분석만 실행 (기존 수집 데이터 기반).

        Args:
            week: 'YYYY-WNN' (예: 2026-W20). None이면 AgentMemory.get_current_week()와 동일하게 달력 주차.
        """
        run_id  = self.memory.start_run("analyze_only")
        t_start = time.time()
        try:
            result = AnalysisAgent(self.memory, week=week).run()
            self.memory.finish_run(run_id, "success", duration=time.time()-t_start)
            return result
        except Exception as e:
            self.memory.finish_run(run_id, "failed", error=str(e), duration=time.time()-t_start)
            raise

    def run_decide(self, week: str | None = None) -> dict:
        """
        DecisionAgent만 실행 (기존 분석 데이터 기반).
        week 지정 시 해당 주차 데이터로 3-step 인사이트 생성 + HTML 리포트 출력.

        Args:
            week: 'YYYY-WNN' (예: 2026-W21). None이면 현재 주차.
        """
        run_id  = self.memory.start_run("decide_only")
        t_start = time.time()
        try:
            agent = DecisionAgent(self.memory)
            if week:
                agent.week = week
            result = agent.run({"week": agent.week, "sns_anomalies": __import__("pandas").DataFrame(), "momentum": __import__("pandas").DataFrame(), "llm_insights": []})
            self.memory.finish_run(run_id, "success", duration=time.time()-t_start)
            return result
        except Exception as e:
            self.memory.finish_run(run_id, "failed", error=str(e), duration=time.time()-t_start)
            raise

    def run_report(self) -> dict:
        """리포트만 생성 (기존 분석 데이터 기반)."""
        run_id  = self.memory.start_run("report_only")
        t_start = time.time()
        try:
            result = DecisionAgent(self.memory).run({})
            self.memory.finish_run(run_id, "success", duration=time.time()-t_start)
            return result
        except Exception as e:
            self.memory.finish_run(run_id, "failed", error=str(e), duration=time.time()-t_start)
            raise

    # ──────────────────────────────────────────
    # 파이프라인 상태 (Airflow DAG 의 check_status 가 사용)
    # ──────────────────────────────────────────
    # 단계별 "완료" 판정 기준 테이블: 해당 주차 행이 있으면 그 단계는 끝난 것으로 본다.
    # (수집은 플랫폼별로 따로 판정 — get_pipeline_status 참고)
    _STEP_TABLES = {
        "analyze": "fact_sns_signals",
        "decide":  "fact_trend_insights",
    }

    def get_pipeline_status(self, week: str | None = None) -> dict:
        """
        주차별 단계 완료 여부.
        반환 예: {"week": "2026-W47", "collect": True, "analyze": True, "decide": False,
                  "collect_tiktok": True, "collect_youtube": True, "collect_oy_global": True}
        """
        week = week or self.memory.get_current_week()
        status: dict = {"week": week}
        with sqlite3.connect(self.db_path, timeout=10) as conn:
            def _has(sql: str, *params) -> bool:
                try:
                    return conn.execute(sql, (week, *params)).fetchone()[0] > 0
                except sqlite3.OperationalError:
                    return False

            for step, table in self._STEP_TABLES.items():
                status[step] = _has(f"SELECT COUNT(*) FROM {table} WHERE week = ?")

            # 수집은 플랫폼별로 따로 판정한다. YouTube 매일 수집이 목요일 전에 그 주 fact_sns_raw 를
            # 미리 채우므로, '원본 한 줄이라도 있으면 수집 끝' 으로 보면 TikTok·OY 수집까지 건너뛴다.
            #   tiktok    : 원본 게시물
            #   youtube   : 주간 집계(aggregate_week) 결과 — 매일 원본만으로는 미완료
            #   oy_global : 랭킹
            status["collect_tiktok"] = _has(
                "SELECT COUNT(*) FROM fact_sns_raw WHERE week = ? AND platform_id = ?", "tiktok")
            status["collect_youtube"] = _has(
                "SELECT COUNT(*) FROM fact_sns_signals WHERE week = ? AND platform_id = ?", "youtube")
            status["collect_oy_global"] = _has(
                "SELECT COUNT(*) FROM fact_retail_rankings WHERE week = ?")
            status["collect"] = (status["collect_tiktok"] and status["collect_youtube"]
                                 and status["collect_oy_global"])
        return status

    def build_catalog(
        self,
        platforms: list[str] = None,
        scrape_ingredients: bool = True,
    ) -> dict:
        """
        카탈로그 초기 구축 (월 1회 권장).
        platforms: ['oy_global', 'sephora'] 또는 None(전체)
        scrape_ingredients: Sephora Playwright 성분 스크랩 여부 (기본 True)
        """
        from tools.collect.oy_global_tool import OYGlobalTool
        from tools.collect.sephora_tool   import SephoraTool

        db     = self.db_path
        result = {}
        targets = platforms or ["oy_global", "sephora"]

        if "oy_global" in targets:
            try:
                logger.info("OY Global 카탈로그 구축 중...")
                r = OYGlobalTool(db_path=db).build_catalog()
                result["oy_global"] = r
                logger.info(f"OY Global 완료: {r}")
            except Exception as e:
                logger.error(f"OY Global 카탈로그 실패: {e}")
                result["oy_global"] = {"error": str(e)}

        if "sephora" in targets:
            try:
                logger.info("Sephora 카탈로그 구축 중...")
                r = SephoraTool(db_path=db).build_catalog(
                    scrape_ingredients=scrape_ingredients
                )
                result["sephora"] = r
                logger.info(f"Sephora 완료: {r}")
            except Exception as e:
                logger.error(f"Sephora 카탈로그 실패: {e}")
                result["sephora"] = {"error": str(e)}

        return result
