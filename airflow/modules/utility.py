from os import environ

from pendulum import timezone

KST_TIMEZONE = timezone("Asia/Seoul")

# [배포] 저장 위치 1/5 — 모든 DAG가 쓰는 Airflow 연결 ID. Airflow UI의 Connection Id와 같은 값이다.
# 연결 이름을 바꾸려면 여기 한 곳이나 `NEWS_CONNECTION_ID` 환경 변수만 고친다.
CONNECTION_ID = environ.get("NEWS_CONNECTION_ID", "finance")

AIRFLOW_HOME = environ.get("AIRFLOW_HOME")
