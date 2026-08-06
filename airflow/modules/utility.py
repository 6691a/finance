from os import environ

from pendulum import timezone

KST_TIMEZONE = timezone("Asia/Seoul")

AIRFLOW_HOME = environ.get("AIRFLOW_HOME")
