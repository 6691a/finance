"""SQL 파일에 psycopg가 자리표시자로 오해할 `%`가 없는지.

**주석 안의 `%`도 자리표시자로 읽힌다.** psycopg는 SQL 전체를 스캔하므로
`-- 등락률(%)이다` 한 줄이 실행 시점에 다음을 낸다.

    ProgrammingError: only '%s', '%b', '%t' are allowed as placeholders, got '%)'

가짜 커서를 쓰는 테스트는 이것을 잡지 못한다 — 실제로 `execute`를 부르는 순간에만 터진다.
2026-08-27에 인과 그래프 SQL 셋이 이 함정을 밟았고, 그래서 이 가드를 둔다.

리터럴 `%`가 정말 필요하면 SQL에서 `%%`로 이스케이프한다. 주석이면 낱말로 바꾸는 편이 낫다.
"""

import pathlib

SQL_ROOT = pathlib.Path(__file__).resolve().parents[2] / "airflow" / "sql"

# psycopg가 허용하는 것: %s, %b, %t, %(name)s, 그리고 이스케이프한 %%.
ALLOWED_AFTER = frozenset("sbt(%")


def _stray_percents(text: str) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for number, line in enumerate(text.splitlines(), 1):
        index = 0
        while (index := line.find("%", index)) != -1:
            following = line[index + 1 : index + 2]
            if following == "%":
                index += 2  # 이스케이프한 한 쌍은 통째로 건너뛴다
                continue
            if following not in ALLOWED_AFTER:
                found.append((number, line.strip()))
            index += 1
    return found


def test_sql_files_have_no_stray_percent_signs():
    offenders: list[str] = []
    for path in sorted(SQL_ROOT.rglob("*.sql")):
        for number, line in _stray_percents(path.read_text()):
            offenders.append(f"{path.relative_to(SQL_ROOT)}:{number}: {line}")

    assert not offenders, (
        "psycopg가 자리표시자로 읽는 `%`가 있다. 주석이면 낱말로 바꾸고, "
        "리터럴이 필요하면 `%%`로 이스케이프한다:\n  " + "\n  ".join(offenders)
    )


def test_the_guard_catches_a_comment_percent():
    """가드 자체가 도는지. 이 검사가 조용히 통과만 하면 있으나 마나다."""
    assert _stray_percents("-- 등락률(%)이다\nSELECT 1") == [(1, "-- 등락률(%)이다")]
    assert _stray_percents("SELECT %(code)s, %s") == []
    assert _stray_percents("SELECT 'a%%b'") == []
