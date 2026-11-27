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


BACKUP_KEEP = 4  # 주간 DB 사본 보관 개수


def backup_db(week: str, keep: int = BACKUP_KEEP) -> Path:
    """
    주간 실행이 성공한 뒤 DB 를 db/backup/kbeauty_<week>.db 로 복사하고 최근 keep 개만 남긴다.
    sqlite3 backup API 를 써서 WAL 내용까지 일관된 사본을 만든다(파일 복사는 쓰는 중이면 깨질 수 있음).
    같은 디스크 사본이라 데이터 실수 복구용이고, 서버 고장 대비는 Lightsail 스냅샷이 맡는다.
    """
    db = KBEAUTY_HOME / "db" / "kbeauty.db"
    out_dir = KBEAUTY_HOME / "db" / "backup"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"kbeauty_{week}.db"
    tmp = out.with_suffix(".db.tmp")
    # sqlite3 연결의 with 는 commit 만 하고 닫지 않으므로 closing 으로 닫는다 (안 닫으면 파일이 잠김)
    from contextlib import closing
    with closing(sqlite3.connect(str(db), timeout=30)) as src, closing(sqlite3.connect(str(tmp))) as dst:
        src.backup(dst)
        ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f"백업 무결성 검사 실패: {ok}")
    tmp.replace(out)
    # 수동 사본(다른 이름)은 건드리지 않고, 주차 이름 사본만 회전
    weekly = sorted(out_dir.glob("kbeauty_[0-9][0-9][0-9][0-9]-W[0-9][0-9].db"))
    for old in weekly[:-keep]:
        old.unlink()
    return out


def notify_failure(context) -> None:
    """
    재시도까지 모두 실패한 task 를 알린다. .env 설정에 따라
    - ALERT_EMAIL_TO + SMTP_USER + SMTP_PASSWORD: 이메일 (Gmail 앱 비밀번호, 알림 전용 계정 권장)
    - SLACK_WEBHOOK_URL: Slack
    둘 다 없으면 로그만 남긴다. 알림 실패가 원래 에러를 가리지 않도록 예외는 삼킨다.
    """
    ti = context.get("task_instance")
    text = (
        f"K-Beauty DAG 실패 — {ti.dag_id}.{ti.task_id} "
        f"(try {ti.try_number}) {context.get('exception')}"
        if ti else "K-Beauty DAG 실패"
    )
    log.error(text)

    to, user, pw = (os.environ.get(k) for k in ("ALERT_EMAIL_TO", "SMTP_USER", "SMTP_PASSWORD"))
    if to and user and pw:
        try:
            import smtplib
            from email.message import EmailMessage
            msg = EmailMessage()
            msg["Subject"] = f"[K-Beauty] {ti.dag_id}.{ti.task_id} 실패" if ti else "[K-Beauty] DAG 실패"
            msg["From"], msg["To"] = user, to
            log_url = getattr(ti, "log_url", "") if ti else ""
            msg.set_content(f"{text[:3000]}\n\n로그: {log_url}\n(서버에서 ssh 터널 후 열기)")
            with smtplib.SMTP_SSL(os.environ.get("SMTP_HOST", "smtp.gmail.com"), 465, timeout=15) as s:
                s.login(user, pw)
                s.send_message(msg)
        except Exception as e:
            log.error(f"이메일 알림 실패: {e}")

    url = os.environ.get("SLACK_WEBHOOK_URL")
    if url:
        try:
            import requests
            requests.post(url, json={"text": f":x: {text[:3000]}"}, timeout=10)
        except Exception as e:
            log.error(f"Slack 알림 실패: {e}")
