"""경로 저장. 어휘 upsert → 경로 → 단계를 한 트랜잭션에 쓴다.

계약은 docs/analysis/market-causal-graph.md 3·6절이다. 실제 DB는 부르지 않는다 —
컬럼 이름과 조인이 맞는지는 운영 DB에 읽기 전용으로 돌려 보는 것이 맡는다(설계 §10.3).
"""

import re
from datetime import date
from typing import Any, Self

import pytest

from modules.causal import domain, store
from modules.causal.domain import NodeChoice, VerifiedPath


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

        outcome = store.store_paths(
            connection,
            window=WINDOW,
            paths=(_path(target_code="KOSPI"),),
            returns=_returns(),
            input_hash="abc",
            llm_run_id=None,
        )

        assert outcome.stored == 0
        assert not any("INSERT INTO market_causal_path" in call[0] for call in connection.calls)

    def test_new_channel_names_are_never_capped(self) -> None:
        """**새 이름 수로 경로를 버리지 않는다**(2026-08-28 제거).

        전에는 상한을 넘긴 경로를 통째로 버렸다. 어휘 위생을 얻고 데이터를 잃는 교환인데,
        어휘 목록은 이미 프롬프트에 **전부** 실려 있어서 모델이 보고도 새 이름을 만들면
        그건 진짜 새 채널로 봐야 한다. 8주 프로토타입은 상한 없이 8개로 수렴했다.
        """
        connection = _connection()
        many = tuple(
            _path(
                channels=(NodeChoice(existing_id="c:9"), NodeChoice(new_name=f"경로{n}")),
                sign="up" if n % 2 else "down",
            )
            for n in range(12)
        )

        outcome = store.store_paths(
            connection,
            window=WINDOW,
            paths=many,
            returns=_returns(),
            input_hash="abc",
            llm_run_id=None,
            require_reuse=True,
        )

        assert outcome.stored == len(many)

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


class TestEvidenceIsStored:
    """모델이 낸 근거를 남긴다. **없으면 `confidence` 판정을 되짚을 수 없다.**

    2026-08-28까지 `verify_paths`가 목록 밖 ref를 버리는 검사까지 하고도 통과한 값을 그대로
    버렸다. 한 실행이 경로 서른넷을 전부 `plausible`로 냈을 때 원인을 가릴 근거가 없었다.
    """

    def test_each_ref_becomes_a_row(self) -> None:
        connection = _connection()

        store.store_paths(
            connection,
            window=WINDOW,
            paths=(_path(evidence_refs=("document:1", "disclosure:2")),),
            returns=_returns(),
            input_hash="abc",
            llm_run_id=None,
        )

        rows = [
            call for call in connection.calls if "INSERT INTO market_causal_evidence" in call[0]
        ]
        assert [call[1][1] for call in rows] == ["document:1", "disclosure:2"]

    def test_a_path_without_evidence_writes_nothing(self) -> None:
        """근거 없는 경로가 정상이다 — 그 주에 평가된 문서가 하나도 없을 수 있다."""
        connection = _connection()

        store.store_paths(
            connection,
            window=WINDOW,
            paths=(_path(evidence_refs=()),),
            returns=_returns(),
            input_hash="abc",
            llm_run_id=None,
        )

        assert not [
            call for call in connection.calls if "INSERT INTO market_causal_evidence" in call[0]
        ]


class TestWeekHasPaths:
    """재실행 판정. 그 주에 행이 있으면 LLM을 다시 부르지 않는다(설계 §5.4)."""

    def test_an_existing_week_is_reported(self) -> None:
        connection = FakeConnection(results={"SELECT EXISTS": [(True,)]})

        assert store.week_has_paths(connection, WINDOW.week_start) is True

    def test_a_fresh_week_is_not(self) -> None:
        connection = FakeConnection(results={"SELECT EXISTS": [(False,)]})

        assert store.week_has_paths(connection, WINDOW.week_start) is False


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


class TestSeedingWeek:
    """어휘가 비어 있는 주는 전부 새로 만들 수밖에 없다.

    2026-08-27 개발 DB 실행에서 첫 주 경로 19개 중 17개가 `MAX_NEW_CHANNELS = 3`에 걸려
    버려졌고, 넓힌 예산 12로도 2026-08-28 운영 실행에서 20개 중 4개가 잘렸다. 두 번 다
    상한이 데이터를 버렸으므로 상한 자체를 걷어냈다 — 폭주는 `VocabularyDriftError`가 막는다.
    """

    def test_the_first_week_stores_everything(self) -> None:
        connection = _connection()
        many = tuple(_path(channels=(NodeChoice(new_name=f"경로{n}"),)) for n in range(20))

        outcome = store.store_paths(
            connection,
            window=WINDOW,
            paths=many,
            returns=_returns(),
            input_hash="abc",
            llm_run_id=None,
            require_reuse=False,  # 어휘가 비어 있다
        )

        assert outcome.stored == len(many)

    def test_the_budget_constants_are_gone(self) -> None:
        """상한이 상수로 남아 있으면 다음 사람이 다시 걸 자리가 생긴다."""
        assert not hasattr(domain, "MAX_NEW_CHANNELS")
        assert not hasattr(domain, "MAX_NEW_CHANNELS_SEED")

    def test_the_outcome_reports_how_much_vocabulary_grew(self) -> None:
        """상한이 사라진 자리에 관측이 들어간다. 매주 늘기만 하면 정규화가 안 되는 것이다."""
        connection = _connection()
        many = (
            _path(channels=(NodeChoice(new_name="가"),)),
            _path(channels=(NodeChoice(new_name="나"),), sign="down"),
        )

        outcome = store.store_paths(
            connection,
            window=WINDOW,
            paths=many,
            returns=_returns(),
            input_hash="abc",
            llm_run_id=None,
            require_reuse=False,
        )

        assert outcome.stored == 2
        assert outcome.new_channels == 2

    def test_the_same_new_name_twice_makes_one_channel(self) -> None:
        """상한이 사라져도 이름 중복은 여전히 한 번만 만든다."""
        connection = _connection()
        many = tuple(_path(channels=(NodeChoice(new_name="같은 이름"),)) for _ in range(4))

        store.store_paths(
            connection,
            window=WINDOW,
            paths=many,
            returns=_returns(),
            input_hash="abc",
            llm_run_id=None,
            require_reuse=False,
        )

        upserts = [call for call in connection.calls if "INSERT INTO market_channel" in call[0]]
        assert len(upserts) == 1


class TestStoreLinkedPaths:
    """대상에서 출발한 경로(설계 §11.4). **`event_id`가 NULL이고 `source_key`가 자연키다.**"""

    def _link(self, **overrides) -> domain.LinkedPath:
        base = {
            "source_target_kind": "quote",
            "source_target_code": "US10Y",
            "source_sign": "down",
            "channels": ("할인율",),
            "target_kind": "instrument",
            "target_code": "005930",
            "sign": "up",
            "confidence": "endpoint_observed",
            "reasoning": "8월 11~13일 국채금리가 내리는 동안 주가가 올랐다",
            "evidence_refs": (),
        }
        return domain.LinkedPath(**(base | overrides))

    def _stored_path(self, connection: FakeConnection) -> tuple:
        for statement, parameters in connection.calls:
            if "INSERT INTO market_causal_path" in statement:
                return parameters
        raise AssertionError("경로가 저장되지 않았다")

    def test_a_linked_path_has_no_event_and_carries_its_source(self) -> None:
        connection = _connection()

        outcome = store.store_paths(
            connection,
            window=WINDOW,
            paths=(),
            returns=_returns(),
            input_hash="h",
            llm_run_id=None,
            links=(self._link(),),
        )

        week, event_id, source_key, source_kind, source_code, source_sign, *_ = self._stored_path(
            connection
        )
        assert week == WINDOW.week_start
        assert event_id is None
        assert source_key == "t:quote:US10Y:down"
        assert (source_kind, source_code, source_sign) == ("quote", "US10Y", "down")
        assert outcome.linked == 1
        assert outcome.stored == 0

    def test_an_event_path_fills_source_key_from_its_event(self) -> None:
        """사건 출발도 같은 칸을 채운다. **자연키가 그 칸 하나만 본다.**"""
        connection = _connection()

        store.store_paths(
            connection,
            window=WINDOW,
            paths=(_path(),),
            returns=_returns(),
            input_hash="h",
            llm_run_id=None,
        )

        _, event_id, source_key, source_kind, source_code, source_sign, *_ = self._stored_path(
            connection
        )
        assert event_id == 1
        assert source_key == "e:1"
        assert (source_kind, source_code, source_sign) == (None, None, None)

    def test_a_linked_path_reuses_a_channel_the_first_answer_just_made(self) -> None:
        """링커는 이름으로만 이을 수 있다. 첫 답이 방금 만든 채널에 같은 노드로 붙어야 한다."""
        connection = _connection()

        outcome = store.store_paths(
            connection,
            window=WINDOW,
            paths=(_path(channels=(NodeChoice(new_name="할인율"),)),),
            returns=_returns(),
            input_hash="h",
            llm_run_id=None,
            links=(self._link(channels=("할인율",)),),
        )

        upserts = [
            parameters
            for statement, parameters in connection.calls
            if "INSERT INTO market_channel" in statement
        ]
        assert upserts == [("할인율", WINDOW.week_start)]
        assert outcome.new_channels == 1

    def test_a_linked_path_without_returns_is_skipped(self) -> None:
        """실현 등락이 없으면 저장할 수 없다(설계 §6). 정상 흐름이라 실패로 만들지 않는다."""
        connection = _connection()

        outcome = store.store_paths(
            connection,
            window=WINDOW,
            paths=(),
            returns=_returns(),
            input_hash="h",
            llm_run_id=None,
            links=(self._link(target_code="KOSPI", target_kind="index"),),
        )

        assert outcome.linked == 0
