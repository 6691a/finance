# DAG가 쓰는 추가 패키지를 담은 Airflow 이미지.
#
# 실행 코드는 굽지 않는다. `airflow/`(컨테이너의 `/opt/airflow`)를 볼륨으로 붙이는 구조를
# 그대로 둔다. 이미지가 책임지는 건 파이썬 패키지와, 그 패키지가 있어야만 쓸 수 있는 OS
# 자원(브리핑 차트의 한글 폰트) 둘뿐이다. **파이썬 패키지를 추가할 때는 Dockerfile이 아니라
# requirements.txt만 고친다.**
#
# `_PIP_ADDITIONAL_REQUIREMENTS`와 달리 컨테이너를 띄울 때마다 설치하지 않는다.
#
# 베이스 이미지 태그와 constraints 파일을 **같은 값 하나**에서 만든다. 둘을 따로 두면
# 베이스만 올렸을 때 옛 버전의 constraints로 패키지를 깔아 이미지가 조용히 어긋난다.
ARG AIRFLOW_VERSION=3.3.0
ARG PYTHON_VERSION=3.13

FROM apache/airflow:${AIRFLOW_VERSION}

# FROM 위의 ARG는 빌드 스테이지 안으로 넘어오지 않는다. 다시 선언한다.
ARG AIRFLOW_VERSION
ARG PYTHON_VERSION

# 브리핑 차트(modules/briefing/chart.py)의 한글 라벨용 폰트. 운영 이미지와 같은 블록이다.
# matplotlib만 넣고 이 폰트가 없으면 chart.py가 ChartError로 멈춰, 로컬에서 차트를 못 보는
# 것은 그대로다(두부 글자로 조용히 나가는 것보다 낫다는 판단이 chart.py에 있다).
# apt는 root가 필요해 잠시 전환한다.
USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-nanum \
    && rm -rf /var/lib/apt/lists/*
USER airflow

COPY requirements.txt /tmp/requirements.txt

# 공식 constraints로 고정한다. 이게 없으면 추가 패키지의 의존성이 Airflow 자신의 핀을
# 밀어내서 이미지가 조용히 깨진다.
RUN pip install --no-cache-dir \
    --constraint "https://raw.githubusercontent.com/apache/airflow/constraints-${AIRFLOW_VERSION}/constraints-${PYTHON_VERSION}.txt" \
    -r /tmp/requirements.txt
