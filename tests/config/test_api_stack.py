"""compose/local/api과 compose/prod/api의 정합.

두 스택은 노출 포트와 DSN만 다르고 이미지·의존성은 같아야 한다. 한쪽만 고치면 개발에서
통과한 코드가 운영 이미지에서 깨진다. `test_realtime_stack.py`와 같은 형태다.

**의존성 목록은 하나다** — 루트 `pyproject.toml`의 `api` 그룹이고 이미지는 `uv sync
--only-group api`로 깐다. requirements.txt를 따로 두던 때 그것이 두 번째 목록이 되어
조용히 어긋났다(2026-08-27, `dependency-injector`).
"""

from pathlib import Path

LOCAL = Path("compose/local/api")
PROD = Path("compose/prod/api")


def payload_lines(path: Path) -> list[str]:
    """주석과 빈 줄을 뺀 실질 내용."""
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.strip().startswith("#")]


def test_dockerfiles_are_identical_between_local_and_prod():
    assert payload_lines(LOCAL / "Dockerfile") == payload_lines(PROD / "Dockerfile")


def test_neither_stack_carries_an_env_file():
    """읽을 DB 별칭은 `apps/api/main.py`의 상수다.

    `read_only` 별칭이 하나뿐이라 개발·운영 어디서나 값이 같다 — 손잡이가 아닌 것을
    환경변수로 두면 `.env` 파일 둘과 그 정합성 검사가 딸려 온다.

    **예외가 하나 있다**(`SENTRY_ENABLED`). 그것은 설정이 아니라 실행 환경의 판단이라
    compose에 인라인으로 적고, 파일로 빼지 않는 규칙은 그대로다.
    """
    for path in (LOCAL, PROD):
        assert not (path / ".env.sample").exists()
        assert "env_file" not in (path / "docker-compose.yaml").read_text()


def test_only_the_prod_stack_turns_sentry_on():
    """**개발 머신의 config.yaml이 운영 것의 사본이다.**

    워크트리에 복사해 쓰는 것이 이 저장소의 관례라 DSN과 `sentry_environment: production`이
    함께 딸려 온다. 진입점의 기본이 "안 보낸다"이고 운영 compose만 켜는 이유가 그것이다 —
    2026-08-27에 로컬 실행이 운영 프로젝트로 트레이스와 로그를 보내고 있었다.

    **이 줄이 빠지면 운영 관측이 조용히 꺼진다.** 그래서 여기서 검사한다.
    """
    import yaml

    prod = yaml.safe_load((PROD / "docker-compose.yaml").read_text())
    local = yaml.safe_load((LOCAL / "docker-compose.yaml").read_text())

    assert prod["services"]["api"]["environment"]["SENTRY_ENABLED"] == "1"
    assert "SENTRY_ENABLED" not in (local["services"]["api"].get("environment") or {})


def test_the_image_runs_the_module_not_a_uvicorn_import_string():
    """`uvicorn apps.api.app:app`은 모듈 수준 `app`을 요구하고, 그러면 import만으로
    config.yaml이 필요해져 "테스트는 설정 파일 없이 import한다"는 규칙이 깨진다."""
    directives = payload_lines(PROD / "Dockerfile")

    assert 'CMD ["python", "-m", "apps.api.main"]' in directives
    # 주석은 그 대안을 설명한다. 실제 CMD가 그것이 아니어야 한다.
    assert not [line for line in directives if line.startswith("CMD") and "uvicorn" in line]


def test_only_the_prod_stack_joins_the_database_network():
    """config.yaml의 DSN이 database 스택의 컨테이너 이름이라 운영은 그 네트워크가 필요하다.
    로컬은 host.docker.internal로 호스트 포트를 본다."""
    prod = (PROD / "docker-compose.yaml").read_text()
    local = (LOCAL / "docker-compose.yaml").read_text()

    assert "external: true" in prod
    assert "host.docker.internal:host-gateway" in local


def test_the_ports_are_published_and_never_bound_to_loopback():
    """내부는 LAN, 외부는 Tailscale이다. 둘 다 NAS 호스트 밖에서 들어온다 —
    `127.0.0.1`로 묶으면 NAS 자기 자신에서만 보인다."""
    def published(path: Path) -> list[str]:
        """`ports:` 블록의 항목만. healthcheck의 URL은 컨테이너 안이라 무관하다."""
        import yaml

        compose = yaml.safe_load((path / "docker-compose.yaml").read_text())
        return [str(entry) for entry in compose["services"]["api"].get("ports", [])]

    assert published(PROD) == ["8000:8000"]
    assert published(LOCAL) == ["18000:8000"]
    # healthcheck의 127.0.0.1은 컨테이너 안이라 무관하다. 매핑에만 없으면 된다.
    assert not [entry for entry in published(PROD) + published(LOCAL) if entry.startswith("127.0.0.1")]


def test_the_code_is_mounted_read_only_not_baked_into_the_image():
    """apps/는 bind-mount다. 그래서 `deploy-api`이 up 뒤에 restart를 부른다."""
    for path in (LOCAL, PROD):
        compose = (path / "docker-compose.yaml").read_text()
        assert "/apps:/app/apps:ro" in compose
        assert "/app/config.yaml:ro" in compose


def test_the_image_group_covers_every_third_party_import_it_runs():
    """이미지가 도는 코드가 import하는 서드파티가 전부 `api` 그룹에 있어야 한다.

    로컬 venv는 그룹 전부를 깔아서 **네이티브 실행은 통과하고 컨테이너만 죽는다.**
    2026-08-27에 `dependency-injector`가 그렇게 빠져 있었다 — 진입점이
    `ModuleNotFoundError`로 죽는 것을 컨테이너를 띄워 보고서야 알았다.
    """
    import ast
    import sys
    import tomllib

    # import 이름과 배포 이름이 다른 것만 적는다. 나머지는 `_`를 `-`로 바꾸면 같다.
    DISTRIBUTION = {"yaml": "pyyaml"}

    def roots(path: Path) -> set[str]:
        found: set[str] = set()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                found |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
        return found

    # 이미지는 `apps/`를 통째로 마운트하지만 진입점이 닿는 것은 이 셋이다.
    imported: set[str] = set()
    for tree in ("apps/api", "apps/core", "apps/models"):
        for path in Path(tree).rglob("*.py"):
            imported |= roots(path)

    third_party = {
        DISTRIBUTION.get(name, name.replace("_", "-"))
        for name in imported
        if name not in sys.stdlib_module_names and name != "apps"
    }
    group = tomllib.loads(Path("pyproject.toml").read_text())["dependency-groups"]["api"]
    declared = {entry.split(">=")[0].split("==")[0].split("[")[0].strip() for entry in group}

    assert third_party <= declared, f"pyproject.toml의 api 그룹에 없다: {sorted(third_party - declared)}"


def test_the_image_installs_from_the_lockfile_not_a_second_list():
    """`uv.lock`이 버전의 원본이다. requirements.txt를 다시 만들면 그것이 두 번째
    목록이 되고, 한쪽만 고친 날 컨테이너에서만 죽는다. Airflow는 운영 이미지에 이미
    깔린 것에 맞춰야 해서 예외다."""
    for path in (LOCAL, PROD):
        directives = payload_lines(path / "Dockerfile")

        assert not (path / "requirements.txt").exists()
        assert "RUN uv sync --frozen --no-install-project --only-group api" in directives


def test_the_image_builds_the_frontend_in_a_node_stage_and_keeps_no_node_at_runtime():
    """운영에는 Python 프로세스 하나만 남는다.

    `frontend/dist`는 Vite 산출물이라 마운트할 소스가 없다 — 그래서 유일하게 굽는 것이고,
    구우려면 Node가 필요한데 그 Node는 build stage에만 있어야 한다.
    """
    for path in (LOCAL, PROD):
        directives = payload_lines(path / "Dockerfile")

        assert "FROM node:24-alpine AS frontend" in directives
        assert "RUN npm ci" in directives
        assert "RUN npm run build" in directives
        assert "COPY --from=frontend /build/dist /app/frontend/dist" in directives
        # 마지막 stage가 Python이어야 런타임에 Node가 없다.
        assert [line for line in directives if line.startswith("FROM ")][-1] == "FROM python:3.13-slim"


def test_both_stacks_build_from_the_repository_root_so_docker_can_read_the_frontend():
    """컨텍스트가 compose 폴더면 `frontend/`가 보이지 않는다."""
    for path in (LOCAL, PROD):
        compose = (path / "docker-compose.yaml").read_text()

        assert "context: ${CODE_DIR:-../../..}" in compose
        assert f"dockerfile: {path}/Dockerfile" in compose


def test_the_dockerignore_sends_the_frontend_source_and_nothing_secret():
    """gitignore 여부와 무관하게 config.yaml·.env·key는 Docker daemon에 보내지 않는다."""
    lines = [line.strip() for line in Path(".dockerignore").read_text().splitlines() if line.strip()]

    assert "*" in lines
    assert "!frontend" in lines
    assert "!pyproject.toml" in lines
    assert "!uv.lock" in lines
    # 이미지 안에서 다시 만드는 것은 컨텍스트로 보내지 않는다.
    assert "frontend/node_modules" in lines
    assert "frontend/dist" in lines
    for secret in ("**/.env", "**/.env.*", "**/config.yaml", "**/*.pem", "**/*.key", "**/id_rsa*"):
        assert secret in lines
    # 실행 코드는 마운트한다. 예외 목록에 apps/가 들어오면 그 계약이 깨진 것이다.
    assert "!apps" not in lines


def test_no_grafana_runtime_or_provisioning_is_left_in_the_repository():
    """Grafana를 더 안 쓰기로 했다(사용자 결정 2026-08-26).

    **과거 결정을 설명하는 마이그레이션 주석은 남긴다** — 왜 quote 뷰를 만들었는지가
    거기 있고, 지우면 그 이유가 사라진다.
    """
    assert not Path("compose/local/grafana").exists()
    assert not Path("tests/dashboards").exists()
    assert "grafana" not in Path("compose/local/docker-compose.yaml").read_text().lower()
    assert "grafana" not in Path("justfile").read_text().lower()
