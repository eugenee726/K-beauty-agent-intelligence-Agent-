"""
kbeauty_common.py — K-Beauty DAG 공용 헬퍼.

DAG 파일은 스케줄러가 자주 파싱하므로, 무거운 import(pandas, playwright,
에이전트 모듈)는 이 모듈의 함수 안에서만 한다.
"""

import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

KBEAUTY_HOME = Path(os.environ.get("KBEAUTY_HOME", Path(__file__).resolve().parents[2]))

log = logging.getLogger(__name__)

DEFAULT_ARGS = {
    "owner":       "kbeauty",
    "retries":     2,
    "retry_delay": timedelta(minutes=10),
}

# 오래 'running' 으로 남은 실행은 프로세스가 죽은 것으로 본다
STALE_RUN_HOURS = 6


def bootstrap() -> None:
    """프로젝트 경로·.env·작업 디렉터리 설정. 각 task 시작 시 호출."""
    home = str(KBEAUTY_HOME)
    if home not in sys.path:
        sys.path.insert(0, home)
    os.chdir(home)  # DB_PATH=db/kbeauty.db 같은 상대경로 대응
    from env_loader import load_env
    load_env()


def orchestrator():
    bootstrap()
    from agents.orchestrator import Orchestrator
    return Orchestrator()


def iso_week(dt: datetime) -> str:
    return dt.strftime("%G-W%V")


def week_for_run(context) -> str:
    """
    이 DAG run 이 다루는 주차. 예약 실행은 data_interval_end(실제 실행 시점)의
    KST 주차라서, 기존 '실행 시점의 달력 주차' 방식과 같다.
    수동 실행에서 conf={"week": "2026-W47"} 을 주면 그 주차를 쓴다.
    """
    dag_run = context["dag_run"]
    conf_week = (dag_run.conf or {}).get("week")
    if conf_week:
        return conf_week
    if dag_run.run_type == "manual":
        # 수동 실행의 data_interval 은 '직전 목요일' 기준이라 월~수에 누르면 지난주가 된다
        return iso_week(datetime.now())
    end = context["data_interval_end"].in_timezone("Asia/Seoul")
    return iso_week(end)


def is_current_week(week: str) -> bool:
    """수집은 '지금' 데이터를 긁으므로 현재 주차일 때만 의미가 있다 (backfill 시 skip)."""
    return week == iso_week(datetime.now())


def mark_stale_runs_failed(context) -> None:
    """
    on_failure_callback: 프로세스가 죽어 'running' 으로 남은 mem_agent_runs 를 failed 로 정리.
    (Orchestrator.run_* 는 예외 시 스스로 failed 를 기록하지만, kill/timeout 은 기록하지 못한다)
    """
    try:
        bootstrap()
        ti = context.get("task_instance")
        msg = f"airflow: {ti.dag_id}.{ti.task_id} failed" if ti else "airflow task failed"
        cutoff = (datetime.now() - timedelta(hours=STALE_RUN_HOURS)).isoformat()
        with sqlite3.connect(str(KBEAUTY_HOME / "db" / "kbeauty.db"), timeout=10) as conn:
            n = conn.execute(
                """UPDATE mem_agent_runs
                   SET status = 'failed', error_message = COALESCE(error_message, ?)
                   WHERE status = 'running' AND run_at < ?""",
                (msg, cutoff),
            ).rowcount
        if n:
            log.warning(f"'running' 으로 남은 실행 {n}건을 failed 로 정리")
    except Exception as e:  # 콜백 실패가 원래 에러를 가리지 않게
        log.error(f"mark_stale_runs_failed 실패: {e}")
    notify_failure(context)


def notify_failure(context) -> None:
    """SLACK_WEBHOOK_URL 이 .env 에 있으면 실패 알림을 보낸다 (없으면 로그만)."""
    url = os.environ.get("SLACK_WEBHOOK_URL")
    ti = context.get("task_instance")
    text = (
        f":x: K-Beauty DAG 실패 — {ti.dag_id}.{ti.task_id} "
        f"(try {ti.try_number}) {context.get('exception')}"
        if ti else ":x: K-Beauty DAG 실패"
    )
    if not url:
        log.error(text)
        return
    try:
        import requests
        requests.post(url, json={"text": text[:3000]}, timeout=10)
    except Exception as e:
        log.error(f"Slack 알림 실패: {e}")
