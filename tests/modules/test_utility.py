"""modules.utility의 트랜잭션 헬퍼 검증."""

import pytest

from modules.utility import atomic


class RecordingConnection:
    """commit·rollback·close 호출 순서를 기록하는 가짜 PEP 249 연결."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def commit(self) -> None:
        self.calls.append("commit")

    def rollback(self) -> None:
        self.calls.append("rollback")

    def close(self) -> None:
        self.calls.append("close")


def test_atomic_commits_on_success() -> None:
    connection = RecordingConnection()
    with atomic(connection) as yielded:
        assert yielded is connection
    assert connection.calls == ["commit"]


def test_atomic_rolls_back_and_reraises_on_error() -> None:
    connection = RecordingConnection()
    with pytest.raises(ValueError, match="boom"), atomic(connection):
        raise ValueError("boom")
    assert connection.calls == ["rollback"]


def test_atomic_does_not_close() -> None:
    connection = RecordingConnection()
    with atomic(connection):
        pass
    assert "close" not in connection.calls
