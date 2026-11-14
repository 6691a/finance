# KIS 실시간 WebSocket 수집기 운영 이미지. 빌드 컨텍스트는 저장소 루트다(CI와 compose 모두).
#
# 실행 코드(apps/)를 굽는다(2026-11-14, k3s 배포). config.yaml은 굽지 않는다 — 운영은
# 비밀값이 든 파일을 /app/config.yaml로 마운트한다. compose로 돌릴 때는 apps/ bind-mount가 덮인다.
# `apps.core.config`가 작업 디렉터리의 config.yaml을 읽으므로 WORKDIR가 /app이어야 한다.
# compose/local/realtime/Dockerfile은 코드를 굽지 않는다 — 로컬은 마운트로 바로 고쳐 돌린다.
FROM python:3.13-slim

COPY compose/prod/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

ENV PYTHONPATH=/app PYTHONUNBUFFERED=1
WORKDIR /app

COPY apps /app/apps

CMD ["python", "-m", "apps.realtime.main"]
