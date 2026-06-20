"""
agent_memory.py
────────────────
K-Beauty Intelligence Agent v3 - 에이전트 메모리 관리

역할:
  - 에이전트 실행 이력 기록 (mem_agent_runs)
  - 키워드 기준선 관리 (이상 탐지용)
  - 플랫폼별 첫 등장 기록 (모멘텀 추적용)
  - DB 초기화 (schema.sql 실행)
"""

import sqlite3
import logging
import uuid
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class AgentMemory:
    """
    에이전트 상태 관리.
    모든 에이전트(Collection, Analysis, Decision)가 공유.
    """

    def __init__(self, db_path: str, schema_path: str):
        self.db_path = db_path
        self.schema_path = schema_path
        self._ensure_db()

    # ──────────────────────────────────────────
    # DB 초기화
    # ──────────────────────────────────────────
    def _ensure_db(self):
        """DB 파일 없으면 schema.sql 실행해서 생성."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            schema = Path(self.schema_path).read_text(encoding="utf-8")
            conn.executescript(schema)
        logger.info(f"DB 초기화 완료: {self.db_path}")

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ──────────────────────────────────────────
    # 실행 이력 관리
    # ──────────────────────────────────────────
    def start_run(self, run_type: str = "manual") -> str:
        """새 실행 이력 생성. run_id 반환."""
        run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO mem_agent_runs (run_id, run_at, run_type, status)
                VALUES (?, ?, ?, 'running')
            """, (run_id, datetime.now().isoformat(), run_type))
        logger.info(f"실행 시작: {run_id}")
        return run_id

    def finish_run(self, run_id: str, status: str, stats: dict = None, error: str = None, duration: float = None):
        """실행 완료 기록. status: 'success' | 'partial' | 'failed'"""
        stats = stats or {}
        with self._conn() as conn:
            conn.execute("""
                UPDATE mem_agent_runs SET
                    status         = ?,
                    sns_records    = ?,
                    retail_records = ?,
                    voc_records    = ?,
                    opportunities  = ?,
                    error_message  = ?,
                    duration_sec   = ?
                WHERE run_id = ?
            """, (
                status,
                stats.get("sns_records",    0),
                stats.get("retail_records", 0),
                stats.get("voc_records",    0),
                stats.get("opportunities",  0),
                error,
                duration,
                run_id,
            ))
        logger.info(f"실행 완료: {run_id} | {status} | {stats}")

    def get_last_run(self) -> dict | None:
        """직전 실행 이력 조회. Planner가 우선순위 결정에 활용."""
        with self._conn() as conn:
            row = conn.execute("""
                SELECT * FROM mem_agent_runs
                ORDER BY run_at DESC LIMIT 1
            """).fetchone()
        return dict(row) if row else None

    # ──────────────────────────────────────────
    # 기준선 관리 (이상 탐지용)
    # ──────────────────────────────────────────
    def get_baseline(self, keyword: str, platform_id: str) -> dict | None:
        """
        키워드 x 플랫폼 기준선 조회 (최근 4주 평균/표준편차).
        Stats Tool이 z-score 계산 시 사용.
        """
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT engagement_score
                FROM fact_sns_signals
                WHERE keyword = ? AND platform_id = ?
                ORDER BY week DESC LIMIT 4
            """, (keyword, platform_id)).fetchall()

        if len(rows) < 2:
            return None

        scores = [r["engagement_score"] for r in rows]
        avg = sum(scores) / len(scores)
        std = (sum((s - avg) ** 2 for s in scores) / len(scores)) ** 0.5
        return {"avg_volume": avg, "std_volume": std}

    # ──────────────────────────────────────────
    # 첫 등장 기록 (모멘텀 추적용)
    # ──────────────────────────────────────────
    def record_first_seen(self, keyword: str, platform_id: str, week: str, score: float):
        """키워드가 플랫폼에서 처음 등장했을 때 기록."""
        with self._conn() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO fact_trend_first_seen
                    (keyword, platform_id, first_seen_week, initial_score)
                VALUES (?, ?, ?, ?)
            """, (keyword, platform_id, week, score))

    def get_first_seen(self, keyword: str) -> list:
        """
        키워드의 플랫폼별 첫 등장 기록 조회.
        Momentum Analyzer가 선행 플랫폼 탐지에 사용.
        """
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT fs.*, dp.platform_type, dp.region
                FROM fact_trend_first_seen fs
                JOIN dim_platform dp ON fs.platform_id = dp.platform_id
                WHERE fs.keyword = ?
                ORDER BY fs.first_seen_week ASC
            """, (keyword,)).fetchall()
        return [dict(r) for r in rows]

    # ──────────────────────────────────────────
    # 유틸리티
    # ──────────────────────────────────────────
    def get_current_week(self) -> str:
        """현재 주차 반환 (ISO 8601). 예: '2026-W19'"""
        return datetime.now().strftime("%G-W%V")

    def get_active_brands(self) -> list:
        """dim_brand 전체 조회."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM dim_brand").fetchall()
        return [dict(r) for r in rows]

    def get_platforms(self, platform_type: str = None) -> list:
        """dim_platform 조회. platform_type: 'sns'|'search'|'retail'|None"""
        with self._conn() as conn:
            if platform_type:
                rows = conn.execute(
                    "SELECT * FROM dim_platform WHERE platform_type = ?",
                    (platform_type,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM dim_platform").fetchall()
        return [dict(r) for r in rows]
