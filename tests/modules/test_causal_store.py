"""경로 저장. 어휘 upsert → 경로 → 단계를 한 트랜잭션에 쓴다.

계약은 docs/analysis/market-causal-graph.md 3·6절이다. 실제 DB는 부르지 않는다 —
컬럼 이름과 조인이 맞는지는 운영 DB에 읽기 전용으로 돌려 보는 것이 맡는다(설계 §10.3).
"""

import re
from datetime import date
from typing import Any, Self

import pytest

from modules.causal import domain, store
from modules.causal.generation import NodeChoice, VerifiedPath


class FakeCursor:
    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection
        self._rows: list[tuple] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: Any = ()) -> None:
        self._connection.calls.append((statement, parameters))
        for key, rows in self._connection.results.items():
            if key in statement:
                self._rows = list(rows)
                return
        self._rows = []

    def fetchone(self) -> tuple | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class FakeConnection:
    def __init__(self, results: dict[str, list[tuple]] | None = None) -> None:
        self.results = results or {}
        self.calls: list[tuple[str, Any]] = []
        self.committed = False
        self.rolled_back = False

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


WINDOW = domain.window_for(date(2026, 8, 10))


def _path(**overrides) -> VerifiedPath:
    base = {
        "event": NodeChoice(new_name="한은 기준금리 인상"),
        "event_date": "2026-08-19",
        "channels": (NodeChoice(new_name="할인율"), NodeChoice(new_name="밸류에이션")),
        "target_kind": "instrument",
        "target_code": "005930",
        "sign": "down",
        "confidence": "observed",
        "reasoning": "금리 인상이 할인율을 높였다",
        "evidence_refs": ("document:84026",),
    }
    return VerifiedPath(**(base | overrides))


def _returns() -> dict[str, domain.TargetReturns]:
    return {
        "005930": domain.TargetReturns(
            week=19.35, t1=-2.18, t5=-6.37, unit=domain.CausalReturnUnit.PERCENT
        )
    }


def _connection(**extra) -> FakeConnection:
    return FakeConnection(
        results={
            "INSERT INTO market_event": [(1,)],
            "INSERT INTO market_channel": [(2,)],
            "INSERT INTO market_causal_path": [(3,)],
            **extra,
        }
    )


class TestChainKey:
    """자연키가 체인을 담는다. 없으면 두 번째 경로가 조용히 삼켜진다(설계 §3.3)."""

    def test_chain_key_follows_the_step_order(self) -> None:
        assert store.chain_key([7, 3]) == "7>3"

    def test_a_different_order_is_a_different_key(self) -> None:
        """`할인율 → 밸류에이션`과 그 반대는 다른 주장이다."""
        assert store.chain_key([7, 3]) != store.chain_key([3, 7])


class TestStorePaths:
    def test_a_new_vocabulary_node_is_inserted_once(self) -> None:
        """같은 채널을 두 경로가 쓰면 마스터에는 한 번만 넣는다."""
        connection = _connection()

        store.store_paths(
            connection,
            window=WINDOW,
            paths=(_path(), _path(target_code="005930", sign="up")),
            returns=_returns(),
            input_hash="abc",
            llm_run_id=None,
        )

        channel_inserts = [
            call for call in connection.calls if "INSERT INTO market_channel" in call[0]
        ]
        assert len(channel_inserts) == 2  # 할인율, 밸류에이션 — 경로 둘이 같은 것을 쓴다

    def test_returns_are_written_with_their_unit(self) -> None:
        connection = _connection()

        store.store_paths(
            connection,
            window=WINDOW,
            paths=(_path(),),
            returns=_returns(),
            input_hash="abc",
            llm_run_id=None,
        )

        path_call = next(
            call for call in connection.calls if "INSERT INTO market_causal_path" in call[0]
        )
        assert "percent" in path_call[1]
        assert 19.35 in path_call[1]

    def test_a_target_without_returns_is_skipped(self) -> None:
        """실현 등락이 없으면 저장할 수 없다(설계 §6)."""
        connection = _connection()

        stored = store.store_paths(
            connection,
            window=WINDOW,
            paths=(_path(target_code="KOSPI"),),
            returns=_returns(),
            input_hash="abc",
            llm_run_id=None,
        )

        assert stored == 0
        assert not any("INSERT INTO market_causal_path" in call[0] for call in connection.calls)

    def test_too_many_new_channels_are_refused(self) -> None:
        """어휘 폭주 가드. 초과분 경로는 저장하지 않는다(설계 §6)."""
        connection = _connection()
        many = tuple(
            _path(channels=(NodeChoice(new_name=f"경로{n}"),), sign="up" if n % 2 else "down")
            for n in range(domain.MAX_NEW_CHANNELS + 2)
        )

        stored = store.store_paths(
            connection,
            window=WINDOW,
            paths=many,
            returns=_returns(),
            input_hash="abc",
            llm_run_id=None,
        )

        assert stored == domain.MAX_NEW_CHANNELS

    def test_an_answer_with_no_reused_channel_fails_the_task(self) -> None:
        """새 이름이 하나도 기존과 안 맞으면 정규화가 깨진 것이다. 조용히 넘어가면 다음 주에
        어휘가 두 배가 된다(설계 §6)."""
        connection = _connection(**{"SELECT id, name": []})
        many = tuple(
            _path(channels=(NodeChoice(new_name=f"경로{n}"),)) for n in range(domain.MAX_CHAIN)
        )

        with pytest.raises(store.VocabularyDriftError):
            store.store_paths(
                connection,
                window=WINDOW,
                paths=many,
                returns=_returns(),
                input_hash="abc",
                llm_run_id=None,
                require_reuse=True,
            )


class TestSqlMatchesTheModel:
    """저장 SQL은 ORM 없이 문자열이다. 컬럼이 어긋나면 실행 시점에야 드러난다.

    이 대조가 없으면 가짜 커서를 쓰는 위 테스트들이 전부 통과하면서 운영에서 죽는다.
    """

    @staticmethod
    def _inserted(statement: str) -> tuple[str, ...]:
        columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
        assert columns is not None
        names = re.sub(r"--[^\n]*", "", columns.group(1))
        return tuple(name.strip() for name in names.split(",") if name.strip())

    @staticmethod
    def _placeholders(statement: str) -> int:
        values = re.search(r"VALUES \(([^)]+)\)", statement, re.DOTALL)
        assert values is not None
        return values.group(1).count("%s")

    @staticmethod
    def _required(table) -> set[str]:
        return {
            column.name
            for column in table.columns
            if not column.nullable and column.server_default is None and not column.primary_key
        }

    @pytest.mark.parametrize(
        ("statement", "model_name"),
        [
            (store.EVENT_UPSERT, "MarketEvent"),
            (store.CHANNEL_UPSERT, "MarketChannel"),
            (store.PATH_INSERT, "MarketCausalPath"),
            (store.STEP_INSERT, "MarketCausalStep"),
        ],
    )
    def test_every_statement_matches_its_table(self, statement: str, model_name: str) -> None:
        from apps.models import analysis

        table = getattr(analysis, model_name).__table__
        columns = self._inserted(statement)

        assert set(columns) <= {column.name for column in table.columns}
        assert self._required(table) <= set(columns)
        assert self._placeholders(statement) == len(columns)

    def test_the_paths_and_masters_return_their_id(self) -> None:
        """부르는 쪽이 id로 단계를 잇는다. RETURNING이 빠지면 조용히 None이 된다."""
        for statement in (store.EVENT_UPSERT, store.CHANNEL_UPSERT, store.PATH_INSERT):
            assert "RETURNING id" in statement

    def test_the_path_insert_never_overwrites(self) -> None:
        """첫 성공본이 불변이다. DO UPDATE면 최초 판단이 사라진다."""
        assert "DO NOTHING" in store.PATH_INSERT

    def test_the_masters_upsert_so_the_id_always_comes_back(self) -> None:
        """마스터는 반대다 — DO NOTHING이면 충돌 시 0행이라 id를 못 받는다."""
        for statement in (store.EVENT_UPSERT, store.CHANNEL_UPSERT):
            assert "DO UPDATE" in statement
            # first_seen_week은 최초 주가 원본이라 덮지 않는다.
            assert "first_seen_week = EXCLUDED" not in statement
