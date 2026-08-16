"""`execute_upserts`가 어느 드라이버의 커서를 받아도 살아남는지 본다."""

import pytest

from modules.upsert import UPSERT_PAGE_SIZE, execute_upserts

STATEMENT = "INSERT INTO t (a) VALUES (%s)"
PARAMETERS = [("x",), ("y",)]


class Psycopg3Cursor:
    """psycopg3 커서. `mogrify`가 없고 psycopg2 커서의 자식도 아니다."""

    def __init__(self):
        self.batches = []

    def execute(self, statement, parameters):
        raise AssertionError("execute_upserts must not fall back to one call per row")

    def executemany(self, statement, parameters):
        self.batches.append((statement, list(parameters)))


def test_a_psycopg3_cursor_falls_back_to_executemany(monkeypatch):
    """psycopg2가 설치돼 있어도 커서가 psycopg3이면 `execute_batch`를 태우면 안 된다.

    둘이 한 이미지에 함께 있으면 import는 성공한다. 그때 커서로 갈리지 않으면
    `execute_batch`가 psycopg3 커서에서 `mogrify`를 찾다 AttributeError로 죽는다.
    """

    def fail(*args, **kwargs):
        raise AssertionError("a psycopg3 cursor must not reach execute_batch")

    monkeypatch.setattr("modules.upsert._execute_batch", fail)
    cursor = Psycopg3Cursor()

    execute_upserts(cursor, STATEMENT, PARAMETERS)

    assert cursor.batches == [(STATEMENT, PARAMETERS)]


def test_a_psycopg2_cursor_takes_the_batch_path(monkeypatch):
    sent = []

    monkeypatch.setattr(
        "modules.upsert._execute_batch",
        lambda cursor, statement, parameters, page_size: sent.append((statement, list(parameters), page_size)),
    )
    monkeypatch.setattr("modules.upsert._Psycopg2Cursor", Psycopg3Cursor)
    cursor = Psycopg3Cursor()

    execute_upserts(cursor, STATEMENT, PARAMETERS)

    assert cursor.batches == []
    assert sent == [(STATEMENT, PARAMETERS, UPSERT_PAGE_SIZE)]


@pytest.mark.parametrize("parameters", [[], ()])
def test_no_parameters_sends_nothing(parameters):
    cursor = Psycopg3Cursor()

    execute_upserts(cursor, STATEMENT, parameters)

    assert cursor.batches == []
