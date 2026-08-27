# 조회 API 운영 이미지.
#
# 실행 코드는 굽지 않는다. 저장소의 apps/와 config.yaml이 /app 아래로 마운트된다.
# `apps.core.config`가 작업 디렉터리의 config.yaml을 읽으므로 WORKDIR가 /app이어야 한다.
# compose/local/api/Dockerfile과 내용이 같다 — 갈라질 이유가 생기기 전까지 함께 고친다.
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
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.9.7 /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock /app/
RUN uv sync --frozen --no-install-project --only-group api

ENV PATH=/app/.venv/bin:$PATH PYTHONPATH=/app PYTHONUNBUFFERED=1

CMD ["python", "-m", "apps.api.main"]
