FROM apache/airflow:3.3.0

RUN pip install --no-cache-dir --upgrade pip
RUN pip install apache-airflow-providers-common-sql

ENV AIRFLOW_HOME=/opt/airflow
ENV PYTHONPATH="${PYTHONPATH}:${AIRFLOW_HOME}"

COPY requirements.txt /requirements.txt
RUN pip install --no-cache-dir -r /requirements.txt