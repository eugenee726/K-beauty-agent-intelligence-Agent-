"""
collection_agent.py
────────────────────
데이터 수집 에이전트 — 트렌드 신호 수집 전용.

책임:
  - SNS(TikTok/YouTube) / OY Global 랭킹 수집
  - 수집 실패 툴 재시도 (최대 2회)

플랫폼 역할:
  · TikTok/YouTube → SNS 버즈 신호 (fact_sns_signals)
  · OY Global      → 판매 기반 랭킹 (fact_retail_rankings)
  · Sephora        → VOC 전용, 수집 단계 제외
                     (분석 단계에서 트렌드 확정 후 on-demand)
                     → tools/collect/voc_collector.py 참고

실행 구조:
  ┌ Phase 1 (병렬) ──────────────────┐
  │  TikTok │ YouTube               │
  └──────────────────────────────────┘
              ↓ 완료 후
  Phase 2 (순차): OliveYoung Global (Playwright)

Phase 2 순차 유지 이유:
  · Playwright 브라우저 프로세스로 자원 소비가 큼
  · dim_product next_product_id 경쟁 없음
"""

import logging
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

from dotenv import load_dotenv

from memory.agent_memory import AgentMemory
from tools.collect.tiktok_tool    import TikTokTool
from tools.collect.youtube_tool   import YouTubeTool
from tools.collect.oy_global_tool import OYGlobalTool

load_dotenv()
logger = logging.getLogger(__name__)


class CollectionAgent:
    """트렌드 신호 수집 조율 에이전트."""

    def __init__(self, memory: AgentMemory):
        self.memory = memory
        db = memory.db_path
        self.week = memory.get_current_week()

        # SQLite WAL 모드 — 멀티스레드 동시 쓰기 시 잠금 경쟁 완화
        self._enable_wal(db)

        self.tiktok = TikTokTool(
            db_path        = db,
            apify_token    = os.getenv("APIFY_TOKEN", ""),
            min_engagement = 50.0,
        )
        self.youtube = YouTubeTool(
            db_path        = db,
            api_key        = os.getenv("YOUTUBE_API_KEY", ""),
            min_engagement = 100.0,
        )
        self.oy_global = OYGlobalTool(db_path=db)

    @staticmethod
    def _enable_wal(db_path: str) -> None:
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()

    def _safe_run(self, name: str, fn, retries: int = 2):
        """툴 실행 래퍼: 예외 발생 시 재시도."""
        for attempt in range(1, retries + 1):
            try:
                result = fn()
                logger.info(f"[{name}] 완료: {result}")
                return result
            except Exception as e:
                logger.warning(f"[{name}] 시도 {attempt}/{retries} 실패: {e}")
        logger.error(f"[{name}] 최종 실패")
        return None

    def run(self, only: list[str] = None) -> dict:
        """
        Orchestrator 호출 진입점.
        only: 실행할 플랫폼 목록 (None이면 전체). 예: ['tiktok', 'youtube']
        반환: 수집 결과 요약 dict
        """
        run_targets = set(only) if only else None
        logger.info(
            f"=== Collection Agent 시작 (week={self.week}"
            + (f", only={sorted(run_targets)}" if run_targets else "")
            + ") ==="
        )
        summary = {
            "week":           self.week,
            "sns_records":    0,
            "retail_records": 0,
        }

        # ── Phase 1: SNS 병렬 수집 ──────────────────────────────────────────
        _p1_all = {
            "TikTok":  lambda: self.tiktok.fetch_and_store(self.week),
            "YouTube": lambda: self.youtube.fetch_and_store(self.week),
        }
        _p1_key_map = {"TikTok": "tiktok", "YouTube": "youtube"}
        phase1_tasks = {
            name: fn for name, fn in _p1_all.items()
            if run_targets is None or _p1_key_map[name] in run_targets
        }

        logger.info(f"--- Phase 1 시작: {' / '.join(phase1_tasks)} 병렬 ---")
        phase1_results: dict[str, object] = {}

        if phase1_tasks:
            with ThreadPoolExecutor(max_workers=len(phase1_tasks)) as pool:
                future_to_name = {
                    pool.submit(self._safe_run, name, fn): name
                    for name, fn in phase1_tasks.items()
                }
                for future in as_completed(future_to_name):
                    name = future_to_name[future]
                    try:
                        phase1_results[name] = future.result()
                    except Exception as e:
                        logger.error(f"[{name}] Future 예외: {e}")
                        phase1_results[name] = None

        tt = phase1_results.get("TikTok")
        if tt:
            summary["sns_records"] += tt

        yt = phase1_results.get("YouTube")
        if yt:
            summary["sns_records"] += yt

        logger.info("--- Phase 1 완료 ---")

        # ── Phase 2: OliveYoung Global (Playwright) ─────────────────────────
        if run_targets is None or "oy_global" in run_targets:
            logger.info("--- Phase 2 시작: OliveYoung Global ---")

            oy_g = self._safe_run("OYGlobal", lambda: self.oy_global.fetch_and_store(self.week))
            if isinstance(oy_g, dict):
                summary["retail_records"] += oy_g.get("rankings", 0)

            logger.info("--- Phase 2 완료 ---")

        logger.info(
            f"=== Collection 완료: SNS {summary['sns_records']}, "
            f"Retail {summary['retail_records']} ==="
        )
        return summary
