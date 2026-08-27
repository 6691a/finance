"""compose/local/api과 compose/prod/api의 정합.

두 스택은 노출 포트와 DSN만 다르고 이미지·의존성은 같아야 한다. 한쪽만 고치면 개발에서
통과한 코드가 운영 이미지에서 깨진다. `test_realtime_stack.py`와 같은 형태다.
"""

from pathlib import Path

LOCAL = Path("compose/local/api")
PROD = Path("compose/prod/api")


def payload_lines(path: Path) -> list[str]:
    """주석과 빈 줄을 뺀 실질 내용."""
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.strip().startswith("#")]


def test_requirements_are_identical_between_local_and_prod():
    assert payload_lines(LOCAL / "requirements.txt") == payload_lines(PROD / "requirements.txt")


def test_dockerfiles_are_identical_between_local_and_prod():
    assert payload_lines(LOCAL / "Dockerfile") == payload_lines(PROD / "Dockerfile")


def test_neither_stack_carries_service_env_knobs():
    """읽을 DB 별칭은 `apps/api/main.py`의 상수다.

    `read_only` 별칭이 하나뿐이라 개발·운영 어디서나 값이 같다 — 손잡이가 아닌 것을
    환경변수로 두면 `.env` 파일 둘과 그 정합성 검사가 딸려 온다.
    """
    for path in (LOCAL, PROD):
        assert not (path / ".env.sample").exists()
        assert "env_file" not in (path / "docker-compose.yaml").read_text()


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
