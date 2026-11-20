"""
kbeauty_weekly — 주간 파이프라인 (매주 목요일 09:00 KST)

check_status ─> [collect_tiktok | collect_youtube | collect_oy_global]
             ─> data_quality_check ─> analyze ─> decide ─> publish_report

- 이미 끝난 단계는 건너뛴다 (주차별 테이블에 데이터가 있으면 완료로 본다).
- 수집은 현재 주차일 때만 실행한다. 과거 주차를 backfill 하면 analyze/decide 만 다시 돈다.
- 수동 실행 시 conf={"week": "2026-W47", "force": true} 로 주차 지정·강제 재실행 가능.
"""

from datetime import timedelta

import pendulum
from airflow.decorators import dag, task
from airflow.exceptions import AirflowSkipException
from airflow.operators.python import get_current_context

from kbeauty_common import (
    DEFAULT_ARGS, bootstrap, is_current_week, mark_stale_runs_failed,
    orchestrator, week_for_run,
)


def _force(context) -> bool:
    return bool((context["dag_run"].conf or {}).get("force"))


@dag(
    dag_id="kbeauty_weekly",
    schedule="0 9 * * 4",
    start_date=pendulum.datetime(2026, 11, 1, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,          # SQLite 동시 쓰기 방지
    default_args={**DEFAULT_ARGS, "on_failure_callback": mark_stale_runs_failed},
    tags=["kbeauty"],
    doc_md=__doc__,
)
def kbeauty_weekly():

    @task
    def check_status() -> dict:
        ctx = get_current_context()
        week = week_for_run(ctx)
        status = orchestrator().get_pipeline_status(week)
        if _force(ctx):
            status.update(collect=False, analyze=False, decide=False)
        status["collect_allowed"] = is_current_week(week)
        return status

    def _collect(platform: str, status: dict) -> dict:
        if status["collect"]:
            raise AirflowSkipException(f"{status['week']} 수집 데이터 이미 있음")
        if not status["collect_allowed"]:
            raise AirflowSkipException(f"{status['week']} 는 과거 주차 → 수집 skip (backfill)")
        result = orchestrator().run_collect(only=[platform])
        key = "retail_records" if platform == "oy_global" else "sns_records"
        if not result.get(key):
            # CollectionAgent 는 플랫폼 오류를 삼키고 0건을 반환하므로 여기서 실패로 올린다
            raise RuntimeError(f"{platform} 수집 0건: {result}")
        return result

    @task(execution_timeout=timedelta(minutes=30))
    def collect_tiktok(status: dict) -> dict:
        return _collect("tiktok", status)

    @task(execution_timeout=timedelta(minutes=30))
    def collect_youtube(status: dict) -> dict:
        return _collect("youtube", status)

    @task(execution_timeout=timedelta(minutes=60))
    def collect_oy_global(status: dict) -> dict:
        return _collect("oy_global", status)

    @task(trigger_rule="none_failed", retries=0)
    def data_quality_check(status: dict) -> dict:
        bootstrap()
        from tools.quality.data_quality import check_week
        report = check_week("db/kbeauty.db", status["week"])
        if not report.ok:
            raise ValueError(f"데이터 품질 검사 실패 {status['week']}: {report.failures}")
        return report.counts

    @task(trigger_rule="none_failed", execution_timeout=timedelta(minutes=45))
    def analyze(status: dict) -> None:
        if status["analyze"]:
            raise AirflowSkipException(f"{status['week']} 분석 이미 완료")
        orchestrator().run_analyze(week=status["week"])

    @task(trigger_rule="none_failed", execution_timeout=timedelta(minutes=60))
    def decide(status: dict) -> str | None:
        if status["decide"]:
            raise AirflowSkipException(f"{status['week']} 인사이트 이미 완료")
        result = orchestrator().run_decide(week=status["week"])
        path = result.get("report_path")
        return str(path) if path else None

    @task(trigger_rule="none_failed")
    def publish_report(report_path: str | None) -> str | None:
        """REPORT_S3_BUCKET 이 설정돼 있으면 HTML 리포트를 S3 에 올린다."""
        import os
        bootstrap()
        bucket = os.environ.get("REPORT_S3_BUCKET")
        if not report_path:
            raise AirflowSkipException("새 리포트 없음")
        if not bucket:
            raise AirflowSkipException(f"REPORT_S3_BUCKET 미설정 — 로컬 리포트: {report_path}")
        import boto3
        from pathlib import Path
        key = f"reports/{Path(report_path).name}"
        boto3.client("s3").upload_file(
            report_path, bucket, key, ExtraArgs={"ContentType": "text/html; charset=utf-8"}
        )
        return f"s3://{bucket}/{key}"

    status = check_status()
    collected = [collect_tiktok(status), collect_youtube(status), collect_oy_global(status)]
    dq = data_quality_check(status)
    collected >> dq
    analyzed = analyze(status)
    dq >> analyzed
    decided = decide(status)
    analyzed >> decided
    publish_report(decided)


kbeauty_weekly()
