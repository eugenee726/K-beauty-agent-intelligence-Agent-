"""
kbeauty_catalog — 월간 제품 카탈로그 재구축 (매월 1일 03:00 KST)

build_oy_global ─> build_sephora
(둘 다 SQLite 에 대량으로 쓰므로 병렬 대신 순차 실행)
"""

from datetime import timedelta

import pendulum
from airflow.decorators import dag, task

from kbeauty_common import DEFAULT_ARGS, mark_stale_runs_failed, orchestrator


def _build(platform: str) -> dict:
    result = orchestrator().build_catalog(platforms=[platform]).get(platform, {})
    # Orchestrator.build_catalog 는 예외를 {"error": ...} 로 삼키므로 실패로 올린다
    if "error" in result:
        raise RuntimeError(f"{platform} 카탈로그 실패: {result['error']}")
    if not result.get("total"):
        raise RuntimeError(f"{platform} 카탈로그 0건: {result}")
    return result


@dag(
    dag_id="kbeauty_catalog",
    schedule="0 3 1 * *",
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args={**DEFAULT_ARGS, "on_failure_callback": mark_stale_runs_failed},
    tags=["kbeauty"],
    doc_md=__doc__,
)
def kbeauty_catalog():

    @task(execution_timeout=timedelta(hours=3))
    def build_oy_global() -> dict:
        return _build("oy_global")

    @task(execution_timeout=timedelta(hours=2))
    def build_sephora() -> dict:
        return _build("sephora")

    build_oy_global() >> build_sephora()


kbeauty_catalog()
