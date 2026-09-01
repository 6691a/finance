"""주간 인과 그래프 한 실행의 조립. DAG이 부르는 유일한 자리다.

계약은 docs/analysis/market-causal-graph.md 2·5·6절이다. 실제 모델도 DB도 부르지 않는다.
"""

from datetime import UTC, date, datetime

import pytest

from modules.causal import domain, run


def _path(**overrides) -> domain.VerifiedPath:
    """모델이 냈다고 치는 경로 하나. **맨 문자열을 쓰지 않는다** — 요약이 `evidence_refs`를
    읽으므로 가짜가 모델이 아니면 인용률 계측을 테스트가 못 본다."""
    base = {
        "event": domain.NodeChoice(new_name="한은 기준금리 인상"),
        "event_date": "2026-08-19",
        "channels": (domain.NodeChoice(new_name="할인율"),),
        "target_kind": "instrument",
        "target_code": "005930",
        "sign": "down",
        "confidence": "observed",
        "reasoning": "금리 인상이 할인율을 높였다",
        "evidence_refs": ("document:1",),
    }
    return domain.VerifiedPath(**(base | overrides))


class FakeStore:
    """`week_has_paths`와 `store_paths`를 대신한다."""

    def __init__(self, already: bool = False) -> None:
        self.already = already
        self.stored: list[tuple] = []
        self.opened: list[dict] = []
        self.closed: list[tuple[int, dict]] = []

    def week_has_paths(self, connection, week_start):
        return self.already

    def store_paths(self, connection, **kwargs):
        self.stored.append(kwargs)
        return domain.StoreOutcome(stored=len(kwargs["paths"]), new_channels=2)

    def start_llm_run(self, connection, **kwargs):
        self.opened.append(kwargs)
        return 501

    def finish_llm_run(self, connection, llm_run_id, **kwargs):
        self.closed.append((llm_run_id, kwargs))


class _FakeModel:
    model_name = "fake-model"


@pytest.fixture
def wiring(monkeypatch: pytest.MonkeyPatch) -> FakeStore:
    """DB·모델을 걷어내고 조립 순서만 남긴다."""
    store = FakeStore()
    monkeypatch.setattr(run, "connection", lambda: _FakeConn())
    monkeypatch.setattr(run.store, "week_has_paths", store.week_has_paths)
    monkeypatch.setattr(run.store, "store_paths", store.store_paths)
    monkeypatch.setattr(run.store, "start_llm_run", store.start_llm_run)
    monkeypatch.setattr(run.store, "finish_llm_run", store.finish_llm_run)
    monkeypatch.setattr(run, "_causal_model", lambda: _FakeModel(), raising=False)
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
    # 종가 블록은 툴박스를 통해 DB를 친다. 조립 순서를 보는 테스트에서는 걷어낸다.
    monkeypatch.setattr(run, "_weekly_closes", lambda toolbox, window, returns: {})
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
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: called.append(kwargs) or _built())

    result = run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
    )

    assert result["skipped"] is True
    assert called == []
    assert wiring.stored == []


def test_a_fresh_week_stores_what_the_model_returned(
    wiring: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: _built(paths=(_path(),)))

    result = run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
    )

    assert result["skipped"] is False
    assert result["week_start"] == "2026-08-10"
    assert result["stored"] == 1
    assert result["new_channels"] == 2


def test_the_first_week_does_not_require_reuse(
    wiring: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """어휘가 비어 있으면 전부 새로 만드는 것이 정상이다. 그때 재사용을 강제하면 언제나 죽는다."""
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: _built(paths=(_path(),)))

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
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: _built(paths=(_path(),)))

    run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
    )

    assert wiring.stored[0]["require_reuse"] is True


def test_the_param_decides_the_week(wiring: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """수동 재실행은 벽시계가 아니라 Param이 정한다."""
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: _built())

    result = run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param="2026-07-06"
    )

    assert result["week_start"] == "2026-07-06"


def test_a_target_without_returns_fails_the_task(
    wiring: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**대상 하나라도 실현 등락이 없으면 죽인다**(2026-08-28).

    8/17 주를 T+5 일봉이 들어오기 전에 돌렸더니 대상 열 중 둘만 남고 경로 여섯이
    저장됐는데 태스크는 성공이었다. 이 DAG은 같은 창을 자동으로 다시 보는 실행이 없어서,
    반쪽짜리 주가 그대로 굳는다. 휴장은 이 판정에 안 섞인다 — SQL이 달력이 아니라
    거래일을 세고 `RETURNS_SCAN_DAYS`가 그 여유를 준다.
    """
    monkeypatch.setattr(
        run.candidates,
        "resolve_targets",
        lambda conn: (
            domain.CausalTarget(kind=domain.CausalTargetKind.INSTRUMENT, code="005930"),
            domain.CausalTarget(kind=domain.CausalTargetKind.INDEX, code="KOSPI"),
        ),
    )
    called = []
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: called.append(1) or _built())

    with pytest.raises(run.IncompleteReturnsError) as error:
        run.build_weekly_graph(
            logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
        )

    assert "KOSPI" in str(error.value)
    # **모델을 부르기 전에 죽는다.** 저장 단계에서 버리면 비용만 쓰고 반쪽을 남긴다.
    assert called == []


def test_no_target_with_returns_fails_too(
    wiring: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """전부 없는 것도 같은 실패다. 전에는 조용히 0건 성공이었다."""
    monkeypatch.setattr(run.candidates, "fetch_returns", lambda conn, targets, window: {})
    called = []
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: called.append(1) or _built())

    with pytest.raises(run.IncompleteReturnsError):
        run.build_weekly_graph(
            logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
        )

    assert called == []


def test_the_builder_gets_a_toolbox(wiring: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """**툴은 연결과 창과 대상 목록을 봐야 한다.** 그것을 쥔 것이 여기뿐이다."""
    seen: list[dict] = []
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: seen.append(kwargs) or _built())

    run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
    )

    # 빌더가 툴박스를 쥐고 온다. 원장이 열리기 전에 빌더가 있어야 모델 이름을 적는다(G-37).
    builder = seen[0]["builder"]
    assert builder._toolbox is not None
    assert builder._toolbox.tools


def test_the_summary_counts_what_the_run_saw(
    wiring: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """반쪽 실행을 실패로 만들었어도 **무엇을 보고 돌았는지는 남아야 한다.**

    8/03 주가 근거 0건으로 돌아간 것을 알아채는 데 후보 조립을 손으로 재현해야 했다.
    """
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: _built(paths=(_path(),)))
    monkeypatch.setattr(
        run.candidates,
        "fetch_candidates",
        lambda conn, targets, window: domain.CandidateSet(
            documents=(
                domain.DocumentCandidate(
                    ref="document:1",
                    title="제목",
                    summary="",
                    source_slug="src",
                    published_at=datetime(2026, 8, 11, 1, 0, tzinfo=UTC),
                    value_score=7,
                    assessed_direction="up",
                ),
            )
        ),
    )

    result = run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
    )

    assert result["targets"] == 1
    assert result["documents"] == 1


def test_the_summary_counts_how_many_candidates_the_model_cited(
    wiring: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**후보에 없어서 못 본 것과 있었는데 안 쓴 것은 다른 문제다.** 앞쪽은 조립 SQL이,
    뒤쪽은 프롬프트가 고치는데 재지 않으면 어느 쪽인지 모른다. 인용률이 낮게 유지되면
    후보를 넓힐 게 아니라 좁혀서 진하게 줘야 한다는 신호다(설계 §8.2).
    """
    monkeypatch.setattr(
        run,
        "_build_paths",
        # 경로 둘이 같은 문서를 인용한다. 인용 **건수**가 아니라 인용된 **후보 수**다.
        lambda **kwargs: _built(
            paths=(
                _path(evidence_refs=("document:1",)),
                _path(evidence_refs=("document:1", "technical_signal:9")),
            )
        ),
    )
    monkeypatch.setattr(
        run.candidates,
        "fetch_candidates",
        lambda conn, targets, window: domain.CandidateSet(
            documents=(
                domain.DocumentCandidate(
                    ref="document:1",
                    title="제목",
                    summary="",
                    source_slug="src",
                    published_at=datetime(2026, 8, 11, 1, 0, tzinfo=UTC),
                    value_score=7,
                    assessed_direction="up",
                ),
            ),
            signals=(
                domain.SignalCandidate(
                    ref="technical_signal:9",
                    target_code="005930",
                    signal_date=date(2026, 8, 12),
                    kind="golden_cross",
                    direction="up",
                ),
                domain.SignalCandidate(
                    ref="technical_signal:10",
                    target_code="005930",
                    signal_date=date(2026, 8, 13),
                    kind="rsi_overbought",
                    direction="down",
                ),
            ),
        ),
    )

    result = run.build_weekly_graph(
        logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC), week_start_param=None
    )

    assert result["candidates"] == 3
    assert result["cited"] == 2


def test_the_input_hash_is_recorded(wiring: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """무엇으로 만들었는지가 행마다 남는다(설계 §5.4)."""
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: _built(paths=(_path(),)))

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


# ---------------------------------------------------------------------------
# G-37 — 경로 생성 대화도 원장 행을 갖는다
# ---------------------------------------------------------------------------


def _built(paths=(), links=(), attempts=0, truncated=False) -> domain.BuildResult:
    return domain.BuildResult(
        paths=tuple(paths), links=tuple(links), attempts=attempts, investigation_truncated=truncated
    )


def _run(**overrides):
    values = {
        "logical_date": datetime(2026, 8, 23, 22, 0, tzinfo=UTC),
        "week_start_param": None,
    }
    values.update(overrides)
    return run.build_weekly_graph(**values)


def test_the_conversation_opens_and_closes_a_ledger_row(
    wiring: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`causal/store.start_llm_run`이 있는데 아무도 안 불렀다. 죽은 대화가 원장에 없으면
    "안 돌았다"와 "돌다 죽었다"를 못 가른다."""
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: _built(paths=(_path(),), truncated=True))

    _run(dag_run_id="manual__1", try_number=2)

    (opened,) = wiring.opened
    assert opened["kind"] == "causal"
    assert opened["run_date"] == date(2026, 8, 10)
    assert (opened["dag_run_id"], opened["try_number"]) == ("manual__1", 2)
    assert opened["llm_model"] == "fake-model"
    assert opened["prompt_version"] == domain.PROMPT_VERSION
    ((run_id, closed),) = wiring.closed
    assert run_id == 501
    assert closed["status"] == "succeeded"
    assert (closed["subjects_requested"], closed["subjects_answered"]) == (1, 1)
    assert closed["investigation_truncated"] is True
    assert wiring.stored[0]["llm_run_id"] == 501


def test_a_conversation_that_dies_still_closes_the_ledger(
    wiring: FakeStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(**kwargs):
        raise RuntimeError("socket hung up")

    monkeypatch.setattr(run, "_build_paths", boom)

    with pytest.raises(RuntimeError, match="socket hung up"):
        _run()

    ((_, closed),) = wiring.closed
    assert closed["status"] == "failed"
    assert "socket hung up" in closed["error"]
    assert wiring.stored == []


def test_two_empty_answers_fail_the_week(wiring: FakeStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """교정 뒤에도 비면 0행을 저장하고 성공했다. "인과가 없었다"와 "모델이 두 번 못 냈다"가
    XCom에서 같은 모양이었다."""
    monkeypatch.setattr(run, "_build_paths", lambda **kwargs: _built(attempts=1))

    with pytest.raises(run.EmptyAnswerError):
        _run()

    ((_, closed),) = wiring.closed
    assert closed["status"] == "failed"
    assert closed["subjects_answered"] == 0
    assert wiring.stored == []
