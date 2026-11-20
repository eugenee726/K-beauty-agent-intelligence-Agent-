"""
env_loader.py
──────────────
프로젝트 .env 를 UTF-8 로 직접 파싱해 os.environ 에 넣는다.
(Windows cp949 환경에서 python-dotenv 가 깨지는 문제 대응)

main.py 와 Airflow DAG(airflow/dags/) 가 함께 사용한다.
"""

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def load_env(env_path: Path | None = None, override: bool = True) -> None:
    """KEY=VALUE 형식의 .env 를 읽는다. 따옴표와 ' #' 뒤 인라인 주석은 제거한다."""
    path = env_path or PROJECT_ROOT / ".env"
    if not path.exists():
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.split(" #")[0].strip().strip('"').strip("'")
            if override or key not in os.environ:
                os.environ[key] = val
