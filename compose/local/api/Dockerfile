# 조회 API 개발 이미지.
#
# 실행 코드는 굽지 않는다. 저장소의 apps/와 config.yaml이 /app 아래로 마운트된다.
# `apps.core.config`가 작업 디렉터리의 config.yaml을 읽으므로 WORKDIR가 /app이어야 한다.
# compose/prod/api/Dockerfile과 내용이 같다 — 갈라질 이유가 생기기 전까지 함께 고친다.
#
# **프런트 빌드 산출물만 예외로 굽는다.** `frontend/dist`는 Vite가 만든 정적 파일이고
# 소스가 아니라서 마운트할 것이 없다. 그래서 Node stage에서 만들고 그 결과만 옮긴다 —
# **런타임에는 Node 프로세스가 없다.** Node 24는 문서 작성 시점의 LTS다.
#
# 프런트가 바뀌면 `just build-api`로 이미지를 다시 만들어야 한다. Python만 바뀌면
# bind-mount라 `just deploy-api`의 restart로 끝난다.
#
# **의존성은 uv가 저장소의 uv.lock에서 깐다.** requirements.txt를 따로 두면 그것이 두
# 번째 의존성 목록이 되어 조용히 어긋난다 — 2026-08-27에 dependency-injector가 그렇게
# 빠져서 컨테이너만 ModuleNotFoundError로 죽었다. Airflow만 requirements.txt를 쓰는데,
# 그건 운영 Airflow 이미지에 이미 깔린 것에 맞춰야 하는 특수한 사정이다.
#
# `--only-group api`가 그 이미지가 쓰는 것만 깐다. `[project].dependencies` 전체를 깔면
# scrapling[fetchers](playwright·patchright)와 ipykernel까지 따라온다.
# `--no-install-project`인 이유는 apps/를 설치하지 않고 마운트해서 PYTHONPATH로 보기 때문이다.
#
# `uvicorn apps.api.app:app`이 아니라 `python -m apps.api.main`이다. 전자는 모듈 수준
# `app = create_app()`을 요구하고, 그러면 import만으로 config.yaml이 필요해져
# "테스트는 설정 파일 없이 이 모듈을 import한다"는 규칙이 그 자리에서 깨진다.
FROM node:24-alpine AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json /build/
RUN npm ci
COPY frontend /build/
RUN npm run build

FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-install-project --only-group api

COPY --from=frontend /build/dist /app/frontend/dist

ENV PATH=/app/.venv/bin:$PATH PYTHONPATH=/app PYTHONUNBUFFERED=1

CMD ["python", "-m", "apps.api.main"]
