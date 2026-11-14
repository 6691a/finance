# 운영 Airflow 이미지. 빌드 컨텍스트는 저장소 루트다(CI와 compose 모두).
#
# 실행 코드(airflow/{dags,modules,sql,plugins,utility})를 굽는다(2026-11-14, k3s 배포).
# 배포 단위가 이미지 태그 하나가 되고 CI(GitHub Actions)가 빌드하므로 NAS buildkit을 쓰지 않는다.
# compose로 돌릴 때는 같은 경로의 bind-mount가 그 위에 덮여 예전처럼 clone 트리를 읽는다.
FROM apache/airflow:3.3.0

# 브리핑 차트(modules/briefing/chart.py)의 한글 라벨용 폰트. 없으면 chart.py가
# ChartError로 멈추고 브리핑은 표만 나간다. apt는 root가 필요해 잠시 전환한다.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-nanum \
    && rm -rf /var/lib/apt/lists/*
USER airflow

RUN pip install --no-cache-dir --upgrade pip
RUN pip install apache-airflow-providers-common-sql

ENV AIRFLOW_HOME=/opt/airflow
ENV PYTHONPATH="${PYTHONPATH}:${AIRFLOW_HOME}"

COPY compose/prod/airflow/requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt

# 저장소의 airflow/가 컨테이너의 /opt/airflow다(docs/operations.md "Airflow와 공유하는 코드").
# config/(airflow.cfg는 추적하지 않음)와 files/·logs/(데이터)는 굽지 않는다.
COPY --chown=airflow:root airflow/dags /opt/airflow/dags
COPY --chown=airflow:root airflow/modules /opt/airflow/modules
COPY --chown=airflow:root airflow/sql /opt/airflow/sql
COPY --chown=airflow:root airflow/plugins /opt/airflow/plugins
COPY --chown=airflow:root airflow/utility /opt/airflow/utility
