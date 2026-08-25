"""compose/local/airflow와 compose/prod/airflow의 정합.

두 이미지는 빌드 방식이 다르다(로컬은 ARG + 공식 constraints, 운영은 provider를 따로 깐다).
같아야 하는 것은 **DAG가 실행 시점에 기대하는 것** 둘이다 — 파이썬 패키지 목록과 브리핑
차트용 한글 폰트. 한쪽만 고치면 로컬에서 나오던 출력이 운영에서 다르게 나온다.

2026-08-25까지 로컬에만 matplotlib이 없어 한국장 브리핑 차트가 로컬에서 항상 ImportError
폴백으로만 돌았고, 그 차이가 어디에도 적혀 있지 않았다.
"""

from pathlib import Path

LOCAL = Path("compose/local/airflow")
PROD = Path("compose/prod/airflow")


def payload_lines(path: Path) -> list[str]:
    """주석과 빈 줄을 뺀 실질 내용."""
    return [line.strip() for line in path.read_text().splitlines() if line.strip() and not line.strip().startswith("#")]


def test_requirements_are_identical_between_local_and_prod():
    assert payload_lines(LOCAL / "requirements.txt") == payload_lines(PROD / "requirements.txt")


def test_both_images_install_the_korean_font():
    """`chart.py`는 한글 폰트가 없으면 두부 글자 대신 ChartError로 멈춘다.

    matplotlib만 맞추고 폰트를 빼면 로컬에서 차트가 안 나오는 것은 그대로다. 실패 이유만
    ImportError에서 ChartError로 바뀐다.
    """
    for path in (LOCAL / "Dockerfile", PROD / "Dockerfile"):
        assert "fonts-nanum" in path.read_text()
