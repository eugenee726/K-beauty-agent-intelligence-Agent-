"""
kbeauty_youtube_daily — YouTube 일간 수집 (매일 17:30 KST, YouTube 할당 초기화(태평양 자정 = 17:00 KST) 직후)

전날(UTC) 하루 동안 올라온 영상만 검색어별로 수집해 fact_sns_raw 에 쌓는다.
- 주 1회 '최근 7일' 검색은 검색어당 상위 50개만 잡혀 표본이 작았다. 하루 단위로 나누면
  같은 할당량 구조에서 표본이 여러 배로 늘고, 인기 상위 영상 편향도 줄어든다.
- 날짜 창이 겹치지 않고 (platform_id, post_id) UNIQUE 라 중복 저장되지 않는다.
- 하루치는 그 날을 분석하는 다음 목요일 실행의 주차로 묶는다(예전 주간 수집과 같은 '목~수 7일').
- 통계(조회수 등)와 키워드 신호는 주간 DAG 의 collect_youtube 가 주말 기준으로 다시 계산한다.
- 할당: 검색어 약 54개 × 100단위 ≈ 하루 5,400단위 (무료 10,000단위 안).
"""

from datetime import timedelta

import pendulum
from airflow.decorators import dag, task
from airflow.operators.python import get_current_context

from kbeauty_common import DEFAULT_ARGS, bootstrap, mark_stale_runs_failed


@dag(
    dag_id="kbeauty_youtube_daily",
    schedule="30 17 * * *",
    start_date=pendulum.datetime(2026, 11, 22, tz="Asia/Seoul"),
    catchup=False,
    max_active_runs=1,
    default_args={**DEFAULT_ARGS, "on_failure_callback": mark_stale_runs_failed},
    tags=["kbeauty"],
    doc_md=__doc__,
)
def kbeauty_youtube_daily():

    @task(execution_timeout=timedelta(minutes=30))
    def collect_yesterday() -> int:
        import os
        bootstrap()
        from tools.collect.youtube_tool import YouTubeTool
        ctx = get_current_context()
        day = ctx["data_interval_end"].in_timezone("UTC").date() - timedelta(days=1)
        return YouTubeTool("db/kbeauty.db", os.environ["YOUTUBE_API_KEY"]).collect_day(day)

    collect_yesterday()


kbeauty_youtube_daily()
