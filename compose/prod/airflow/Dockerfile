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

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt