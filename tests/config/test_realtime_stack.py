"""compose/local/realtime와 compose/prod/realtime의 정합.

두 스택은 DSN과 로깅만 다르고 이미지·의존성은 같아야 한다. 한쪽만 고치면
개발에서 통과한 코드가 운영 이미지에서 깨진다.
"""

from pathlib import Path

LOCAL = Path("compose/local/realtime")
PROD = Path("compose/prod")


def payload_lines(path: Path) -> list[str]:
    """주석과 빈 줄을 뺀 실질 내용."""
    return [
        line.strip()
        for line in path.read_text().splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_requirements_are_identical_between_local_and_prod():
    assert payload_lines(LOCAL / "requirements.txt") == payload_lines(PROD / "requirements.txt")


def test_dockerfiles_are_identical_between_local_and_prod():
    assert payload_lines(LOCAL / "Dockerfile") == payload_lines(PROD / "Dockerfile")


def test_env_samples_share_the_same_variable_names():
    def names(path: Path) -> list[str]:
        return [line.split("=", 1)[0] for line in payload_lines(path / ".env.sample")]

    assert names(LOCAL) == names(PROD)
