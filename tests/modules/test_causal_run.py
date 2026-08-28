"""주간 인과 그래프 한 실행의 조립. DAG이 부르는 유일한 자리다.

계약은 docs/analysis/market-causal-graph.md 2·5·6절이다. 실제 모델도 DB도 부르지 않는다.
"""

from datetime import UTC, date, datetime

import pytest

from modules.causal import domain, run


class FakeStore:
    """`week_has_paths`와 `store_paths`를 대신한다."""

    def __init__(self, already: bool = False) -> None:
        self.already = already
        self.stored: list[tuple] = []

    def week_has_paths(self, connection, week_start):
        return self.already

    def store_paths(self, connection, **kwargs):
        self.stored.append(kwargs)
        return len(kwargs["paths"])


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """DB·모델을 걷어내고 조립 순서만 남긴다."""
    store = FakeStore()
    monkeypatch.setattr(run, "connection", lambda: _FakeConn())
    monkeypatch.setattr(run.store, "week_has_paths", store.week_has_paths)
    monkeypatch.setattr(run.store, "store_paths", store.store_paths)
    monkeypatch.setattr(
        run.candidates,
        "resolve_targets",
        lambda conn: (
            domain.CausalTarget(kind=domain.CausalTargetKind.INSTRUMENT, code="005930"),
        ),
    )
    monkeypatch.setattr(
        run.candidates,
        "fetch_returns",
        lambda conn, targets, window: {
            "005930": domain.TargetReturns(
                week=19.35, t1=-2.18, t5=-6.37, unit=domain.CausalReturnUnit.PERCENT
            )
        },
    )
    monkeypatch.setattr(
        run.candidates, "fetch_candidates", lambda conn, targets, window: domain.CandidateSet()
    )
    monkeypatch.setattr(run.candidates, "fetch_vocabulary", lambda conn, window: ((), ()))
    return store


class _FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def close(self):
        return None


def test_a_week_that_already_has_paths_does_not_call_the_model(
    wiring: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """첫 성공본이 불변이다. 재실행은 기존 행을 두고 성공으로 끝난다(설계 §5.4·§10.4)."""
    wiring.already = True
    called = []
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: called.append(kwargs) or ())

    result = run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
    )

    assert result["skipped"] is True
    assert called == []
    assert wiring.stored == []


def test_a_fresh_week_stores_what_the_model_returned(
    wiring: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: ("path",))

    result = run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
    )

    assert result["skipped"] is False
    assert result["week_start"] == "2026-08-10"
    assert result["stored"] == 1


def test_the_first_week_does_not_require_reuse(
    wiring: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """어휘가 비어 있으면 전부 새로 만드는 것이 정상이다. 그때 재사용을 강제하면 언제나 죽는다."""
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: ("path",))

    run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
    )

    assert wiring.stored[0]["require_reuse"] is False


def test_a_week_with_existing_vocabulary_requires_reuse(
    wiring: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """어휘가 쌓인 뒤에도 전부 새 이름이면 정규화가 깨진 것이다(설계 §6)."""
    monkeypatch.setattr(
        run.candidates,
        "fetch_vocabulary",
        lambda conn, window: ((), (domain.ChannelOption(node_id="c:1", name="할인율"),)),
    )
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: ("path",))

    run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
    )

    assert wiring.stored[0]["require_reuse"] is True


def test_the_param_decides_the_week(wiring: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """수동 재실행은 벽시계가 아니라 Param이 정한다."""
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: ())

    result = run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param="2026-07-06"
    )

    assert result["week_start"] == "2026-07-06"


def test_no_target_with_returns_is_reported_not_crashed(
    wiring: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실현 등락이 하나도 없으면 LLM을 부를 이유가 없다. 정상 흐름이라 죽이지 않는다."""
    monkeypatch.setattr(run.candidates, "fetch_returns", lambda conn, targets, window: {})
    called = []
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: called.append(1) or ())

    result = run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
    )

    assert result["stored"] == 0
    assert called == []


def test_the_input_hash_is_recorded(wiring: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """무엇으로 만들었는지가 행마다 남는다(설계 §5.4)."""
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: ("path",))

    run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
    )

    expected = domain.input_hash(
        week_start=date(2026, 8, 10), target_codes=["005930"], candidate_refs=[]
    )
    assert wiring.stored[0]["input_hash"] == expected


def test_it_uses_the_shared_connection_id():
    """연결 id를 새로 만들지 않는다. 저장소 전체가 `modules/utility.py`의 상수 하나를 쓴다.

    2026-08-28 운영 트리거가 `AirflowNotFoundException: conn_id 'finance_db' isn't defined`로
    죽었다 — 이름을 지어냈고 그 이름의 Connection이 운영에 없었다.
    """
    from modules.utility import CONNECTION_ID

    assert run.CONNECTION_ID is CONNECTION_ID
