# 시장 추론 조회 API 운영 이미지.
#
# 실행 코드는 굽지 않는다. 저장소의 apps/와 config.yaml이 /app 아래로 마운트된다.
# `apps.core.config`가 작업 디렉터리의 config.yaml을 읽으므로 WORKDIR가 /app이어야 한다.
# compose/local/api/Dockerfile과 내용이 같다 — 갈라질 이유가 생기기 전까지 함께 고친다.
#
# `uvicorn apps.api.app:app`이 아니라 `python -m apps.api.main`이다. 전자는 모듈 수준
# `app = create_app()`을 요구하고, 그러면 import만으로 config.yaml이 필요해져
# "테스트는 설정 파일 없이 이 모듈을 import한다"는 규칙이 그 자리에서 깨진다.
FROM python:3.13-slim

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
WORKDIR /app

CMD ["python", "-m", "apps.api.main"]
