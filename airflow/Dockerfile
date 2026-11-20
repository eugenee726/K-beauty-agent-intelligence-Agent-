# K-Beauty Intelligence Agent — Airflow 이미지
# apache/airflow 공식 이미지 + 프로젝트 의존성 + Playwright Chromium
FROM apache/airflow:2.10.5-python3.12

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright

USER root
RUN mkdir -p ${PLAYWRIGHT_BROWSERS_PATH} && chown airflow: ${PLAYWRIGHT_BROWSERS_PATH}

USER airflow
COPY requirements.txt /tmp/requirements.txt
# airflow 버전을 같이 명시해 프로젝트 의존성이 airflow 를 업/다운그레이드하지 못하게 한다
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" -r /tmp/requirements.txt \
 && python -m playwright install chromium

# Chromium 실행에 필요한 OS 라이브러리 (root 권한 필요)
USER root
RUN PYTHONPATH=$(su airflow -c "python -c 'import site; print(site.getusersitepackages())'") \
    python -m playwright install-deps chromium \
 && rm -rf /var/lib/apt/lists/*

USER airflow
