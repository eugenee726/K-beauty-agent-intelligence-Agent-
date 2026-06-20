"""
scheduler.py
──────────────
K-Beauty Intelligence Agent v3 — 정기 실행 스케줄러.

스케줄:
  - 매주 목요일 09:00  → 전체 파이프라인 (agentic: 수집→분석→결정→리포트)
  - 매월 1일   03:00  → 카탈로그 재구축 (build_catalog)

실행:
  python scheduler.py            # 포그라운드 상주 (Ctrl+C 종료)
  python scheduler.py --now full # 스케줄 무시하고 전체 파이프라인 1회 즉시 실행
  python scheduler.py --now catalog

타임존: Asia/Seoul (KST). 변경하려면 TIMEZONE 상수 수정.

참고: 이 프로세스가 떠 있어야 스케줄이 동작한다. 서버/PC가 꺼지면 실행되지 않으므로,
      장기 운영 시 OS 레벨 스케줄러(Windows 작업 스케줄러 / cron)로
      `python main.py agentic`를 직접 거는 방식도 고려할 것.
"""

import argparse
import logging
import sys
from pathlib import Path

# ── 프로젝트 루트 sys.path 등록 + .env 로드 (main.py와 동일 방식) ──
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

import os

_env_path = PROJECT_ROOT / ".env"
if _env_path.exists():
    with open(_env_path, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _, _v = _line.partition("=")
                _v = _v.split(" #")[0].strip().strip('"').strip("'")
                os.environ[_k.strip()] = _v

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from main import setup_logging
from agents.orchestrator import Orchestrator

logger = logging.getLogger("scheduler")

TIMEZONE = "Asia/Seoul"


# ──────────────────────────────────────────
# 작업 정의
# ──────────────────────────────────────────
def job_full_pipeline() -> None:
    """매주 목요일: 전체 agentic 파이프라인."""
    logger.info("=" * 55)
    logger.info("[스케줄] 주간 전체 파이프라인 시작 (목요일)")
    logger.info("=" * 55)
    try:
        orch = Orchestrator()
        result = orch.run_full_agentic()
        logger.info(f"[스케줄] 주간 파이프라인 완료: {result}")
    except Exception as e:
        logger.error(f"[스케줄] 주간 파이프라인 실패: {e}", exc_info=True)


def job_build_catalog() -> None:
    """매월 1일: 카탈로그 재구축."""
    logger.info("=" * 55)
    logger.info("[스케줄] 월간 카탈로그 재구축 시작 (1일)")
    logger.info("=" * 55)
    try:
        orch = Orchestrator()
        result = orch.build_catalog()
        logger.info(f"[스케줄] 카탈로그 재구축 완료: {result}")
    except Exception as e:
        logger.error(f"[스케줄] 카탈로그 재구축 실패: {e}", exc_info=True)


# ──────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="kbeauty-scheduler",
        description="K-Beauty 정기 실행 스케줄러",
    )
    parser.add_argument(
        "--now",
        choices=["full", "catalog"],
        default=None,
        help="스케줄 무시하고 해당 작업을 1회 즉시 실행 후 종료",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()
    setup_logging(args.log_level)

    # 즉시 실행 모드
    if args.now == "full":
        job_full_pipeline()
        return
    if args.now == "catalog":
        job_build_catalog()
        return

    # 상주 스케줄러 모드
    scheduler = BlockingScheduler(timezone=TIMEZONE)

    # 매주 목요일 09:00
    scheduler.add_job(
        job_full_pipeline,
        CronTrigger(day_of_week="thu", hour=9, minute=0),
        id="weekly_full_pipeline",
        name="주간 전체 파이프라인 (목요일 09:00)",
        misfire_grace_time=3600,   # 1시간 내 지연 실행 허용
        coalesce=True,             # 밀린 실행은 1회로 합침
    )

    # 매월 1일 03:00
    scheduler.add_job(
        job_build_catalog,
        CronTrigger(day=1, hour=3, minute=0),
        id="monthly_build_catalog",
        name="월간 카탈로그 재구축 (1일 03:00)",
        misfire_grace_time=3600,
        coalesce=True,
    )

    logger.info("스케줄러 시작 (타임존: %s)", TIMEZONE)
    for job in scheduler.get_jobs():
        logger.info("  등록됨: %s | 다음 실행: %s", job.name, job.next_run_time)
    logger.info("Ctrl+C 로 종료")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("스케줄러 종료")


if __name__ == "__main__":
    main()
