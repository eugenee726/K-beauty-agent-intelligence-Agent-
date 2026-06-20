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

Agentic 모드 (run_full_agentic):
  Claude API Tool Use를 사용해 LLM이 DB 상태를 보고
  어떤 단계를 실행할지 스스로 판단하는 ReAct 루프.
"""

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
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
    # Agentic 파이프라인 (Claude API Tool Use)
    # ──────────────────────────────────────────

    _AGENTIC_TOOLS = [
        {
            "name": "check_db_status",
            "description": (
                "현재 DB에 어떤 주차 데이터가 저장되어 있는지 확인한다. "
                "각 테이블별 최신 week 값과 행 수를 반환한다. "
                "파이프라인 시작 전 어떤 단계를 실행해야 할지 판단할 때 호출한다."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
        {
            "name": "collect_data",
            "description": (
                "SNS(TikTok/YouTube), OliveYoung Global, Sephora 데이터를 수집해 "
                "fact_raw_posts / fact_raw_products 테이블에 저장한다. "
                "DB에 이번 주 수집 데이터가 없을 때 호출한다."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "only": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["tiktok", "youtube", "oy_global"]},
                        "description": "특정 플랫폼만 수집. 생략 시 전체.",
                    }
                },
                "required": [],
            },
        },
        {
            "name": "analyze_data",
            "description": (
                "수집된 데이터를 통계 분석한다 (z-score 이상 탐지, 모멘텀 계산, LLM 인사이트). "
                "결과는 fact_keyword_stats / fact_product_stats 테이블에 저장된다. "
                "수집 단계가 완료된 후 분석 데이터가 없을 때 호출한다."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "week": {
                        "type": "string",
                        "description": "분석 대상 주차 (예: 2026-W23). 생략 시 현재 주차.",
                    }
                },
                "required": [],
            },
        },
        {
            "name": "decide_insights",
            "description": (
                "분석 결과를 바탕으로 트렌드 인사이트(TrendInsight), 제품 인사이트(ProductInsight), "
                "방한픽(InboundPick)을 생성하고 HTML 리포트를 만든다. "
                "분석 단계가 완료된 후 호출한다."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "week": {
                        "type": "string",
                        "description": "대상 주차 (예: 2026-W23). 생략 시 현재 주차.",
                    }
                },
                "required": [],
            },
        },
    ]

    def _get_db_status(self) -> dict:
        """현재 DB 상태를 조회해 반환."""
        try:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            status = {}

            checks = [
                ("fact_sns_raw",          "week"),
                ("fact_sns_signals",      "week"),
                ("fact_retail_rankings",  "week"),
                ("fact_llm_insights",     "week"),
                ("fact_trend_insights",   "week"),
                ("fact_product_insights", "week"),
                ("fact_inbound_picks",    "week"),
            ]
            for table, week_col in checks:
                try:
                    row = conn.execute(
                        f"SELECT {week_col}, COUNT(*) as cnt "
                        f"FROM {table} GROUP BY {week_col} ORDER BY {week_col} DESC LIMIT 1"
                    ).fetchone()
                    status[table] = {"latest_week": row[week_col], "rows": row["cnt"]} if row else {"latest_week": None, "rows": 0}
                except Exception:
                    status[table] = {"latest_week": None, "rows": 0}

            status["current_week"] = self.memory.get_current_week()
            conn.close()
            return status
        except Exception as e:
            return {"error": str(e)}

    def _execute_agentic_tool(self, tool_name: str, tool_input: dict) -> dict:
        """Agentic 루프에서 Claude가 요청한 툴을 실행한다."""
        if tool_name == "check_db_status":
            return self._get_db_status()

        elif tool_name == "collect_data":
            only = tool_input.get("only") or None
            try:
                result = CollectionAgent(self.memory).run(only=only)
                return {"status": "ok", **result}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        elif tool_name == "analyze_data":
            week = tool_input.get("week") or None
            try:
                result = AnalysisAgent(self.memory, week=week).run()
                return {"status": "ok", "week": result.get("week"), "summary": str(result)[:500]}
            except Exception as e:
                return {"status": "error", "error": str(e)}

        elif tool_name == "decide_insights":
            week = tool_input.get("week") or None
            try:
                result = self.run_decide(week=week)
                return {
                    "status": "ok",
                    "week": result.get("week"),
                    "trend_insights": result.get("trend_insights", 0),
                    "product_insights": result.get("product_insights", 0),
                    "inbound_picks": result.get("inbound_picks", 0),
                    "report_path": str(result.get("report_path")) if result.get("report_path") is not None else None,
                }
            except Exception as e:
                return {"status": "error", "error": str(e)}

        else:
            return {"status": "error", "error": f"알 수 없는 툴: {tool_name}"}

    def run_full_agentic(self, week: str | None = None) -> dict:
        """
        Claude API Tool Use 기반 Agentic 파이프라인.
        LLM이 DB 상태를 확인하고 필요한 단계를 스스로 결정해 실행한다.
        LangFuse가 설정되어 있으면 자동으로 트레이싱된다.

        Args:
            week: 목표 주차 (예: '2026-W23'). None이면 현재 주차.
        """
        import anthropic

        # LangFuse 초기화 (LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 없으면 조용히 스킵)
        langfuse = None
        trace    = None
        try:
            import os
            if os.environ.get("LANGFUSE_PUBLIC_KEY"):
                from langfuse import Langfuse
                # LANGFUSE_BASE_URL → LANGFUSE_HOST 자동 매핑
                if os.environ.get("LANGFUSE_BASE_URL") and not os.environ.get("LANGFUSE_HOST"):
                    os.environ["LANGFUSE_HOST"] = os.environ["LANGFUSE_BASE_URL"]
                langfuse = Langfuse()
                logger.info("LangFuse 트레이싱 활성화")
        except ImportError:
            pass

        run_id  = self.memory.start_run("agentic_full")
        t_start = time.time()

        target_week = week or self.memory.get_current_week()
        logger.info(f"Agentic 파이프라인 시작 | 목표 주차: {target_week}")

        # LangFuse 최상위 트레이스 생성
        if langfuse:
            trace = langfuse.trace(
                name   = "kbeauty-agentic-pipeline",
                input  = {"week": target_week},
                tags   = ["agentic", "orchestrator"],
                metadata = {"run_id": run_id},
            )

        client = anthropic.Anthropic()

        system_prompt = (
            "당신은 K-Beauty 인텔리전스 에이전트의 오케스트레이터입니다. "
            f"목표: {target_week} 주차의 트렌드 리포트를 생성한다.\n\n"
            "반드시 아래 규칙을 따르라:\n"
            "1. 먼저 check_db_status를 호출해 현재 DB 상태를 확인한다.\n"
            f"2. fact_sns_raw의 latest_week가 {target_week}이면 collect_data를 건너뛴다.\n"
            f"3. fact_llm_insights의 latest_week가 {target_week}이면 analyze_data를 건너뛴다.\n"
            f"4. fact_trend_insights의 latest_week가 {target_week}이면 decide_insights를 건너뛴다.\n"
            "5. 필요한 단계만 순서대로(collect → analyze → decide) 실행한다.\n"
            "6. 모든 단계가 완료되면 결과를 JSON으로 요약한다."
        )

        messages = [
            {
                "role": "user",
                "content": (
                    f"목표 주차 {target_week}에 대한 K-Beauty 트렌드 리포트를 생성해 주세요. "
                    "DB 상태를 확인한 후 필요한 단계를 실행하세요."
                ),
            }
        ]

        final_result = {}
        max_iterations = 10  # 무한 루프 방지

        for iteration in range(max_iterations):
            logger.info(f"Agentic 루프 #{iteration + 1}")

            # LangFuse: LLM 호출 스팬 시작
            llm_span = None
            if trace:
                llm_span = trace.generation(
                    name        = f"llm-call-{iteration + 1}",
                    model       = "claude-opus-4-8",
                    input       = messages,
                    metadata    = {"iteration": iteration + 1},
                )

            t_llm = time.time()
            response = client.messages.create(
                model="claude-opus-4-8",
                max_tokens=4096,
                thinking={"type": "adaptive"},
                system=system_prompt,
                tools=self._AGENTIC_TOOLS,
                messages=messages,
            )
            llm_latency = round(time.time() - t_llm, 2)

            logger.info(f"  stop_reason: {response.stop_reason} | latency: {llm_latency}s")

            # thinking 블록 추출
            thinking_text = ""
            for block in response.content:
                if hasattr(block, "thinking") and block.thinking:
                    thinking_text = block.thinking
                    logger.info(f"  thinking: {thinking_text[:200]}")

            # LangFuse: LLM 스팬 종료
            if llm_span:
                llm_span.end(
                    output   = {"stop_reason": response.stop_reason, "thinking": thinking_text[:500]},
                    usage    = {
                        "input":  response.usage.input_tokens,
                        "output": response.usage.output_tokens,
                    },
                    metadata = {"latency_s": llm_latency},
                )

            # LLM이 완료했다고 판단
            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text"):
                        logger.info(f"  최종 응답: {block.text[:300]}")
                        try:
                            text  = block.text
                            start = text.find("{")
                            end   = text.rfind("}") + 1
                            if start >= 0 and end > start:
                                final_result = json.loads(text[start:end])
                        except Exception:
                            final_result["summary"] = block.text
                break

            # LLM이 툴 호출 요청
            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        logger.info(f"  툴 실행: {block.name}({block.input})")

                        # LangFuse: 툴 실행 스팬
                        tool_span = None
                        if trace:
                            tool_span = trace.span(
                                name  = f"tool-{block.name}",
                                input = block.input,
                            )

                        t_tool = time.time()
                        result = self._execute_agentic_tool(block.name, block.input)
                        tool_latency = round(time.time() - t_tool, 2)

                        logger.info(f"  툴 결과: {str(result)[:200]}")

                        if tool_span:
                            tool_span.end(
                                output   = result,
                                metadata = {
                                    "latency_s": tool_latency,
                                    "status":    result.get("status", "ok"),
                                },
                            )

                        tool_results.append({
                            "type":        "tool_result",
                            "tool_use_id": block.id,
                            "content":     json.dumps(result, ensure_ascii=False),
                        })

                messages.append({"role": "user", "content": tool_results})
            else:
                logger.warning(f"  예상치 못한 stop_reason: {response.stop_reason}")
                break

        duration = round(time.time() - t_start, 1)
        self.memory.finish_run(run_id, "success", final_result, duration=duration)

        # LangFuse: 최상위 트레이스 종료
        if trace:
            trace.update(
                output   = final_result,
                metadata = {"duration_s": duration, "iterations": iteration + 1},
            )
        if langfuse:
            langfuse.flush()  # 버퍼 강제 전송

        logger.info(f"Agentic 파이프라인 완료 | {duration}s")
        return {
            "run_id":   run_id,
            "status":   "success",
            "duration": duration,
            "week":     target_week,
            **final_result,
        }

    def build_catalog(
        self,
        platforms: list[str] = None,
        scrape_ingredients: bool = True,
    ) -> dict:
        """
        카탈로그 초기 구축 (월 1회 권장).
        platforms: ['oy_global', 'oy_kr', 'sephora'] 또는 None(전체)
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
