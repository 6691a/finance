"""전망·관찰의 검증 — 무엇을 버리고 무엇을 남기나.

**이 파일이 지키는 것은 "모델이 값을 냈다"와 "그 값이 맞다"의 구분이다.** 이유가 요인을
인용하면 이번 실행에서 그것을 봤어야 하고, 관찰은 툴로 조회한 요인만 남는다. 그 검증이
없으면 모델이 관계 가중치만 보고 어제 것을 오늘 것으로 다시 쓴다.

가짜 모델과 가짜 연결을 쓴다. 실 DB도 실 LLM도 부르지 않는다(프로젝트 규칙).
"""

import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from modules.kospi.domain import (
    MAX_ACTIVE_MEMORIES,
    MAX_UNREVIEWED,
    Direction,
    Factor,
    KospiError,
    MemoryVerdict,
    ObservationSign,
    RetireReason,
    RunSlot,
)
from modules.kospi.generation import ForecastBuilder, ReviewBuilder
from modules.kospi.graph import StoredMemory
from modules.kospi.review import plan_memories
from modules.kospi.state import MemoryRow, ObservedState, RelationRow, ReviewState


class FakeToolbox:
    """`KospiToolbox`가 답변 검증에 주는 것은 `queried_factors` 하나다."""

    def __init__(self, queried: set[Factor] | None = None) -> None:
        self.queried_factors = frozenset(queried or set())
        self.tools = []
        self.round_count = 0
        self.tool_calls = ()

    def close_open_records(self) -> None:
        pass


class FakeModel:
    """그래프를 안 도는 테스트용. `parse`만 부르므로 호출되지 않는다."""

    model_name = "fake"


def observed_state(**overrides) -> ObservedState:
    base = {
        "run_date": date(2026, 9, 2),
        "slot": RunSlot.PRE_OPEN,
        "as_of_kst": "2026-09-02 08:35 KST",
        "base_price": Decimal("2650.00"),
        "base_at_kst": "2026-09-01 15:30 KST",
        "base_note": "직전 거래일 확정 종가",
        "relations": (
            RelationRow(factor=Factor.SP500, label="S&P500", weight=0.6, n_obs=8),
            RelationRow(factor=Factor.VIX, label="VIX", weight=0.0, n_obs=0),
        ),
        "memories": (
            MemoryRow(id=17, created_on=date(2026, 9, 1), text="목요일 미국 CPI 발표"),
        ),
    }
    base.update(overrides)
    return ObservedState(**base)


def forecast_builder(*, queried: set[Factor] | None = None, **state) -> ForecastBuilder:
    builder = ForecastBuilder.__new__(ForecastBuilder)
    observed = observed_state(**state)
    builder._observed = observed
    builder._toolbox = FakeToolbox(queried)
    builder._state_factors = frozenset(row.factor for row in observed.relations if row.n_obs > 0)
    builder._memory_ids = frozenset(row.id for row in observed.memories)
    builder._earlier_slots = frozenset(row.slot for row in observed.earlier_slots)
    return builder


def answer(**overrides) -> str:
    body = {"direction": "up", "expected_change_pct": 0.8, "band_pct": 0.5, "reasons": []}
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


# --- 전망 검증 --------------------------------------------------------------


def test_a_reason_citing_a_queried_factor_survives():
    builder = forecast_builder(queried={Factor.US10Y})
    draft = builder.parse(
        answer(reasons=[{"factor": "US10Y", "direction": "down", "statement": "금리가 밤사이 6bp 올랐다"}])
    )
    assert [reason.factor for reason in draft.reasons] == [Factor.US10Y]
    assert draft.rejected == 0
    assert draft.weak is False


def test_a_reason_citing_an_unqueried_factor_is_dropped_and_counted():
    """**분모가 있어야 근거 유효율이 읽힌다.** 로그로만 남기면 버린 수가 사라진다."""
    builder = forecast_builder(queried=set())
    draft = builder.parse(
        answer(reasons=[{"factor": "US10Y", "direction": "down", "statement": "금리가 올랐다"}])
    )
    assert draft.reasons == ()
    assert draft.rejected == 1
    # 이유가 0건이면 약한 답이다. 정상 답과 같아 보이면 안 된다.
    assert draft.weak is True


def test_a_factor_present_in_the_relation_table_can_be_cited_without_a_tool_call():
    builder = forecast_builder(queried=set())
    draft = builder.parse(
        answer(reasons=[{"factor": "SP500", "direction": "up", "statement": "관계가 꾸준히 양이다"}])
    )
    assert len(draft.reasons) == 1


def test_a_factor_with_no_observations_cannot_be_cited():
    # 관측 0인 요인은 "관계 없음"이 아니라 "아직 모른다"라, 그것을 근거로 쓸 수 없다.
    builder = forecast_builder(queried=set())
    draft = builder.parse(
        answer(reasons=[{"factor": "VIX", "direction": "down", "statement": "관계가 없다"}])
    )
    assert draft.rejected == 1


def test_a_memory_outside_the_active_list_is_dropped():
    builder = forecast_builder()
    draft = builder.parse(
        answer(reasons=[{"memory_id": 999, "direction": "up", "statement": "지난 메모를 근거로"}])
    )
    assert draft.rejected == 1


def test_an_active_memory_can_be_cited():
    builder = forecast_builder()
    draft = builder.parse(
        answer(reasons=[{"memory_id": 17, "direction": "down", "statement": "CPI 앞두고 관망"}])
    )
    assert draft.reasons[0].memory_id == 17


def test_a_slot_ref_must_point_at_an_earlier_slot_today():
    from modules.kospi.state import EarlierSlot

    earlier = EarlierSlot(
        slot=RunSlot.PRE_OPEN,
        as_of_kst="2026-09-02 08:35 KST",
        direction=Direction.UP,
        expected_change_pct=Decimal("1.0"),
        band_pct=Decimal("0.5"),
        base_price=Decimal(2650),
    )
    builder = forecast_builder(slot=RunSlot.MIDDAY, earlier_slots=(earlier,))
    kept = builder.parse(
        answer(reasons=[{"slot_ref": "pre_open", "direction": "up", "statement": "장전 판단 유지"}])
    )
    assert kept.reasons[0].slot_ref is RunSlot.PRE_OPEN

    dropped = builder.parse(
        answer(reasons=[{"slot_ref": "pre_close", "direction": "up", "statement": "아직 안 온 슬롯"}])
    )
    assert dropped.rejected == 1


def test_investment_advice_is_dropped_by_code_not_only_by_the_prompt():
    """**프롬프트에만 있는 금지는 가드레일이 아니다.** 코드가 안 보면 어겼는지도 모른다."""
    builder = forecast_builder(queried={Factor.SAMSUNG})
    draft = builder.parse(
        answer(reasons=[{"factor": "SAMSUNG", "direction": "up", "statement": "목표가 9만원을 제시한다"}])
    )
    assert draft.rejected == 1


def test_a_direction_that_disagrees_with_the_sign_is_refused():
    """조용히 부호를 뒤집으면 모델이 부르지 않은 숫자가 채점된다."""
    builder = forecast_builder()
    with pytest.raises(KospiError, match="부호"):
        builder.parse(answer(direction="down", expected_change_pct=1.2))


def test_a_size_outside_the_range_is_refused_instead_of_clamped():
    builder = forecast_builder()
    with pytest.raises(KospiError, match="범위 밖"):
        builder.parse(answer(expected_change_pct=42.0))


def test_the_band_lower_bound_is_enforced():
    builder = forecast_builder()
    with pytest.raises(KospiError, match="범위 밖"):
        # 폭 0은 "정확히 맞힌다"는 뜻이라 거짓이다.
        builder.parse(answer(band_pct=0.0))


def test_reasons_have_no_upper_limit_and_keep_their_order():
    """**개수 상한이 없다.** 순서가 곧 중요도이고 그대로 저장된다."""
    builder = forecast_builder(queried={Factor.US10Y, Factor.SOX, Factor.WTI, Factor.NASDAQ})
    reasons = [
        {"factor": "US10Y", "direction": "down", "statement": "첫째"},
        {"factor": "SOX", "direction": "up", "statement": "둘째"},
        {"factor": "WTI", "direction": "up", "statement": "셋째"},
        {"factor": "NASDAQ", "direction": "up", "statement": "넷째"},
    ]
    draft = builder.parse(answer(reasons=reasons))
    assert [reason.statement for reason in draft.reasons] == ["첫째", "둘째", "셋째", "넷째"]


# --- 관찰 검증 --------------------------------------------------------------


def review_builder(*, queried: set[Factor] | None = None, memories=()) -> ReviewBuilder:
    builder = ReviewBuilder.__new__(ReviewBuilder)
    observed = ReviewState(
        run_date=date(2026, 9, 2),
        as_of_kst="2026-09-02 19:00 KST",
        close=Decimal("2676.50"),
        previous_close=Decimal("2650.00"),
        change_pct=Decimal("1.00"),
        memories=memories,
    )
    builder._observed = observed
    builder._toolbox = FakeToolbox(queried)
    builder._memory_ids = frozenset(row.id for row in memories)
    return builder


def review_answer(**overrides) -> str:
    body = {"observations": [], "memories": [], "memory_reviews": []}
    body.update(overrides)
    return json.dumps(body, ensure_ascii=False)


def test_an_observation_needs_a_tool_call_behind_it():
    """관찰이 숫자를 봤다는 증거가 원장의 툴 호출 목록이다."""
    builder = review_builder(queried=set())
    draft = builder.parse(
        review_answer(
            observations=[{"factor": "US10Y", "sign": "inverse", "strength": 2, "note": "금리가 올랐다"}]
        )
    )
    assert draft.observations == ()
    assert draft.rejected == 1


def test_a_queried_factor_observation_survives():
    builder = review_builder(queried={Factor.US10Y})
    draft = builder.parse(
        review_answer(
            observations=[{"factor": "US10Y", "sign": "inverse", "strength": 2, "note": "금리 +6bp"}]
        )
    )
    assert draft.observations[0].sign is ObservationSign.INVERSE
    assert draft.observations[0].strength == 2


def test_a_duplicate_observation_keeps_the_first_one():
    # 하루에 요인당 엣지 하나다. 어느 쪽이 진짜인지 우리가 고를 수 없다.
    builder = review_builder(queried={Factor.SOX})
    draft = builder.parse(
        review_answer(
            observations=[
                {"factor": "SOX", "sign": "same", "strength": 3, "note": "첫째"},
                {"factor": "SOX", "sign": "inverse", "strength": 1, "note": "둘째"},
            ]
        )
    )
    assert len(draft.observations) == 1
    assert draft.observations[0].note == "첫째"
    assert draft.rejected == 1


def test_a_memory_review_for_an_unknown_id_is_dropped():
    builder = review_builder(memories=(MemoryRow(id=1, created_on=date(2026, 9, 1), text="있는 것"),))
    draft = builder.parse(
        review_answer(memory_reviews=[{"id": 999, "verdict": "drop", "reason": "없는 메모"}])
    )
    assert draft.reviews == ()


# --- 메모의 수명 ------------------------------------------------------------


def memory(memory_id: int, created: str, unreviewed: int = 0) -> StoredMemory:
    return StoredMemory(
        id=memory_id, created_on=date.fromisoformat(created), text=f"메모 {memory_id}", unreviewed_count=unreviewed
    )


def draft_with(**overrides):
    from modules.kospi.generation import ReviewDraft

    return ReviewDraft(**overrides)


def test_the_age_limit_beats_the_model_verdict():
    """**나이 상한이 모델 판정보다 앞선다.** 메모는 규칙이 아니라 '요즘 볼 것'이다."""
    from modules.kospi.generation import MemoryReview

    plan = plan_memories(
        draft_with(reviews=(MemoryReview(id=1, verdict=MemoryVerdict.KEEP, reason="아직 유효"),)),
        active=[memory(1, "2026-08-01")],
        as_of_date=date(2026, 9, 2),
    )
    assert [item.reason for item in plan["retired"]] == [RetireReason.EXPIRED]
    assert plan["kept"] == []


def test_a_missing_verdict_counts_as_unreviewed_not_as_keep():
    plan = plan_memories(
        draft_with(),
        active=[memory(1, "2026-09-01")],
        as_of_date=date(2026, 9, 2),
    )
    assert plan["unreviewed"] == [1]
    assert plan["kept"] == []
    assert plan["retired"] == []


def test_two_consecutive_misses_retire_the_memory():
    plan = plan_memories(
        draft_with(),
        active=[memory(1, "2026-09-01", unreviewed=MAX_UNREVIEWED - 1)],
        as_of_date=date(2026, 9, 2),
    )
    assert [item.reason for item in plan["retired"]] == [RetireReason.UNREVIEWED]


def test_a_dropped_memory_keeps_the_model_reason():
    from modules.kospi.generation import MemoryReview

    plan = plan_memories(
        draft_with(reviews=(MemoryReview(id=1, verdict=MemoryVerdict.DROP, reason="지지선이 깨졌다"),)),
        active=[memory(1, "2026-09-01")],
        as_of_date=date(2026, 9, 2),
    )
    retired = plan["retired"][0]
    assert retired.reason is RetireReason.DROPPED
    assert retired.note == "지지선이 깨졌다"


def test_the_active_cap_rejects_new_memories_and_counts_them():
    from modules.kospi.generation import DraftMemory, MemoryReview

    active = [memory(index, "2026-09-01") for index in range(1, MAX_ACTIVE_MEMORIES + 1)]
    plan = plan_memories(
        draft_with(
            memories=tuple(DraftMemory(text=f"새 메모 {n}", reason="") for n in range(3)),
            reviews=tuple(MemoryReview(id=item.id, verdict=MemoryVerdict.KEEP, reason="") for item in active),
        ),
        active=active,
        as_of_date=date(2026, 9, 2),
    )
    assert plan["new"] == []
    assert plan["rejected"] == 3


def test_retiring_a_memory_frees_a_slot_for_a_new_one():
    """상한은 **이번에 내리는 것을 뺀 뒤** 센다. 지운 자리에 새로 쓰는 것이 정상 흐름이다."""
    from modules.kospi.generation import DraftMemory, MemoryReview

    active = [memory(index, "2026-09-01") for index in range(1, MAX_ACTIVE_MEMORIES + 1)]
    plan = plan_memories(
        draft_with(
            memories=(DraftMemory(text="새 메모", reason="자리가 났다"),),
            reviews=(
                MemoryReview(id=1, verdict=MemoryVerdict.DROP, reason="끝났다"),
                *[MemoryReview(id=item.id, verdict=MemoryVerdict.KEEP, reason="") for item in active[1:]],
            ),
        ),
        active=active,
        as_of_date=date(2026, 9, 2),
    )
    assert len(plan["new"]) == 1
    assert plan["rejected"] == 0


def test_a_duplicate_memory_is_not_written_twice():
    from modules.kospi.generation import DraftMemory, MemoryReview

    active = [StoredMemory(id=1, created_on=date(2026, 9, 1), text="목요일 밤 미국 CPI 발표")]
    plan = plan_memories(
        draft_with(
            memories=(DraftMemory(text="목요일밤 미국 CPI 발표!", reason=""),),
            reviews=(MemoryReview(id=1, verdict=MemoryVerdict.KEEP, reason=""),),
        ),
        active=active,
        as_of_date=date(2026, 9, 2),
    )
    assert plan["new"] == []
    assert plan["rejected"] == 1


# --- 조용한 성공 ------------------------------------------------------------


def test_a_big_move_with_no_observations_kills_the_task():
    from modules.kospi.review import _require_observations

    with pytest.raises(KospiError, match="관찰이 0건"):
        _require_observations(draft_with(), change=Decimal("1.50"))


def test_a_quiet_day_may_have_no_observations():
    from modules.kospi.review import _require_observations

    _require_observations(draft_with(), change=Decimal("0.10"))


def test_the_slot_table_matches_the_intraday_cron():
    """**두 곳을 같은 커밋에서 만진다.** 어긋나면 `resolve_slot`이 실행을 죽인다."""
    import re
    from pathlib import Path

    from modules.kospi.domain import INTRADAY_SLOTS, SLOT_TIMES

    source = Path(__file__).resolve().parents[2] / "airflow" / "dags" / "kospi_intraday_daily.py"
    crons = re.findall(r'"(\d+) (\d+) \* \* 1-5"', source.read_text(encoding="utf-8"))
    scheduled = {(int(hour), int(minute)) for minute, hour in crons}
    assert scheduled == {(SLOT_TIMES[slot].hour, SLOT_TIMES[slot].minute) for slot in INTRADAY_SLOTS}


def test_the_forecast_dag_cron_matches_the_pre_open_slot():
    import re
    from pathlib import Path

    from modules.kospi.domain import SLOT_TIMES

    source = Path(__file__).resolve().parents[2] / "airflow" / "dags" / "kospi_forecast_daily.py"
    minute, hour = re.search(r'schedule="(\d+) (\d+) \* \* 1-5"', source.read_text(encoding="utf-8")).groups()
    assert (int(hour), int(minute)) == (SLOT_TIMES[RunSlot.PRE_OPEN].hour, SLOT_TIMES[RunSlot.PRE_OPEN].minute)


def test_a_ledger_row_survives_a_failed_conversation():
    """**"안 돌았다"와 "돌다 죽었다"를 가르는 유일한 장치다.**"""
    from modules.kospi.tool_ledger import ToolCallLedger

    ledger = ToolCallLedger()
    ledger.begin_round([{"name": "factor_history", "args": {"factor": "US10Y"}, "id": "call_1"}])
    ledger.close_open_records()
    record = ledger.calls[0]
    assert record.tool_name == "factor_history"
    # 결과도 오류도 없는 행은 DB CHECK를 어긴다. 닫아야 저장할 수 있다.
    assert record.error is not None


def test_the_ledger_keeps_the_arguments_the_model_actually_sent():
    from modules.kospi.tool_ledger import ToolCallLedger

    ledger = ToolCallLedger()
    ledger.begin_round([{"name": "recent_news", "args": {"hours": 999}, "id": "call_1"}])
    assert ledger.calls[0].arguments == {"hours": 999}
    # 검증 후 인자는 함수에 닿아야 채워진다. 여기서는 아직 없다.
    assert ledger.calls[0].validated_arguments is None


def test_the_requested_at_is_timezone_aware_utc():
    from modules.kospi.tool_ledger import ToolCallLedger

    ledger = ToolCallLedger()
    ledger.begin_round([{"name": "recent_news", "args": {}, "id": "call_1"}])
    requested = ledger.calls[0].requested_at
    assert requested.tzinfo is not None
    assert requested.utcoffset() == datetime.now(UTC).utcoffset()


# --- 미래 누수 ---------------------------------------------------------------


def test_the_relation_query_cuts_on_creation_time_not_the_observed_date():
    """**같은 날 저녁의 관찰이 그날 아침 전망에 보이면 안 된다.**

    `OBSERVED.date`는 관찰한 거래일이고 엣지는 그날 19:00에 만들어진다. 날짜로 자르면
    장전 전망이 미래를 본다 — 운영에서는 장후가 아직 안 돌아 우연히 안 물리지만, 과거를
    다시 돌리는 순간 백테스트가 통째로 부푼다.
    """
    from modules.kospi.graph import READ_OBSERVATIONS

    assert "o.created_at <= $as_of_at" in READ_OBSERVATIONS
    assert "o.date <= " not in READ_OBSERVATIONS


def test_the_memory_query_cuts_on_creation_time_and_keeps_what_was_live_then():
    """메모도 같은 컷오프를 쓰고, **그때 살아 있던 것은 나중에 내려갔어도 보인다.**"""
    from modules.kospi.graph import READ_MEMORIES

    assert "m.created_at <= $as_of_at" in READ_MEMORIES
    # 그 시점 이후에 내려간 메모는 그때 활성이었다.
    assert "m.retired_on >= $as_of_date" in READ_MEMORIES
    # `created_at`이 없는 옛 행은 보수적으로 — 날짜가 엄격히 앞선 것만.
    assert "m.created_on < $as_of_date" in READ_MEMORIES


def test_new_memories_carry_a_creation_timestamp():
    """`created_at`을 안 쓰면 다음 날 조회가 그 메모를 영영 못 본다(옛 행 규칙에 걸린다)."""
    from modules.kospi.graph import WRITE_MEMORIES, WRITE_MEMORIES_UNLINKED

    for statement in (WRITE_MEMORIES, WRITE_MEMORIES_UNLINKED):
        assert "created_at: $created_at" in statement


def test_the_observed_state_passes_the_timestamp_cutoff_to_the_graph():
    """`common`이 날짜만 넘기면 위 쿼리가 파라미터를 못 받아 죽는다. 그 배선을 잠근다."""
    import inspect

    from modules.kospi import common

    source = inspect.getsource(common.build_observed_state)
    assert "as_of_at=as_of_at" in source
    assert source.count("as_of_at=as_of_at") >= 2


# --- 크기 기준선이 프롬프트에 닿나 ------------------------------------------


def test_the_forecast_prompt_points_at_the_measured_baseline():
    """**판 2의 핵심이다.** 프롬프트가 `moves`를 안 가리키면 그 블록은 실려도 안 읽힌다."""
    from modules.kospi.generation import FORECAST_PROMPTS

    system = FORECAST_PROMPTS.system
    assert "moves" in system
    for field in ("up_median", "down_median", "abs_p50", "abs_p75", "abs_p90", "up_day_ratio"):
        assert field in system, field
    # 기저율을 오늘의 근거로 쓰지 못하게 막는 문장이 있어야 한다.
    assert "기저율이지 오늘의 근거가 아니다" in system


def test_the_observed_state_carries_the_baseline():
    import inspect

    from modules.kospi import common

    assert "moves=store.move_baseline(" in inspect.getsource(common.build_observed_state)


# --- 툴 원장이 실제 실행을 기록하나 ------------------------------------------


class _RecordingCursor:
    """가짜 커서. `factor_history(US10Y)`가 읽는 quote_daily 모양의 행 둘을 준다."""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, statement, parameters=()):
        self.rows = [
            (date(2026, 9, 1), Decimal("4.758"), None, None),
            (date(2026, 9, 2), Decimal("4.796"), Decimal("0.038"), Decimal("0.80")),
        ]

    def executemany(self, statement, parameters):
        return None

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _RecordingConnection:
    def cursor(self):
        return _RecordingCursor()


def test_a_tool_call_through_the_toolbox_lands_in_the_ledger_with_its_result():
    """**이 테스트가 없어서 원장이 69회를 전부 거짓으로 적었다**(2026-09-03).

    `tool_call_id`가 인자 스키마에 없으면 래퍼가 기록을 못 찾아 결과를 못 채우고,
    `finish_round`가 그것을 "함수에 못 닿음"으로 읽어 `validation` 오류로 분류한다. 툴은
    정상이고 모델도 답을 받으므로 태스크는 성공이다 — 원장만 조용히 틀린다.
    """
    from modules.kospi.toolbox import KospiToolbox

    toolbox = KospiToolbox(_RecordingConnection(), as_of_at=datetime(2026, 9, 2, 23, 35, tzinfo=UTC))
    body = toolbox.run("factor_history", {"factor": "US10Y", "days": 5})

    assert '"factor": "US10Y"' in body
    record = toolbox.tool_calls[0]
    assert record.error_kind is None, record.error
    assert record.error is None
    assert record.result == body
    assert record.result_chars == len(body)
    assert record.validated_arguments == {"factor": "US10Y", "days": 5}
    assert record.duration_ms is not None
    assert record.delivered is True
    # 조회한 요인이 검증 재료에 들어간다.
    assert Factor.US10Y in toolbox.queried_factors


def test_the_injected_call_id_is_hidden_from_the_model():
    """`tool_call_id`는 모델에게 안 보인다. 보이면 모델이 위조해 보낼 수 있고 스키마가 지저분해진다."""
    from modules.kospi.toolbox import KospiToolbox

    toolbox = KospiToolbox(_RecordingConnection(), as_of_at=datetime(2026, 9, 2, 23, 35, tzinfo=UTC))
    for tool in toolbox.tools:
        assert "tool_call_id" not in tool.tool_call_schema.model_json_schema().get("properties", {}), tool.name


# --- 모자란 답은 한 번 되묻는다 -------------------------------------------------


class _ScriptedModel:
    """대본대로 답하는 모델. 툴 왕복 없이 곧장 답 JSON을 낸다."""

    model_name = "scripted"

    def __init__(self, *replies: str) -> None:
        self._replies = list(replies)
        self.calls = 0

    def bind_tools(self, tools):
        return self

    def bind(self, **kwargs):
        return self

    def invoke(self, messages):
        from langchain_core.messages import AIMessage

        self.calls += 1
        index = min(self.calls - 1, len(self._replies) - 1)
        return AIMessage(content=self._replies[index])


def _forecast_builder_with(model) -> ForecastBuilder:
    from modules.kospi.toolbox import KospiToolbox

    toolbox = KospiToolbox(_RecordingConnection(), as_of_at=datetime(2026, 9, 2, 23, 35, tzinfo=UTC))
    return ForecastBuilder(model, toolbox, observed=observed_state())


def test_a_forecast_whose_reasons_were_all_dropped_is_asked_again_once():
    """**설계 §6.1이 약속한 것이다.** 이유가 전부 버려지면 한 번 되묻는다."""
    bad = answer(reasons=[{"factor": "WTI", "direction": "up", "statement": "안 부른 요인"}])
    good = answer(reasons=[{"factor": "SP500", "direction": "up", "statement": "관계 표에 있는 요인"}])
    model = _ScriptedModel(bad, good)

    draft = _forecast_builder_with(model).build()

    # 조사(bad) → 답 재사용 실패 → 되묻기 → 조사(good) → 답 재사용.
    assert model.calls == 2
    assert draft.weak is False
    assert [reason.factor for reason in draft.reasons] == [Factor.SP500]


def test_a_second_weak_answer_is_kept_as_weak_not_asked_again():
    """교정은 한 번뿐이다. 두 번째도 모자라면 `weak`로 저장한다 — 태스크를 죽이지 않는다."""
    bad = answer(reasons=[{"factor": "WTI", "direction": "up", "statement": "안 부른 요인"}])
    model = _ScriptedModel(bad, bad, bad)

    draft = _forecast_builder_with(model).build()

    assert model.calls == 2
    assert draft.weak is True
    assert draft.rejected == 1


def test_the_repair_message_carries_why_each_reason_was_dropped():
    """사유 없는 교정을 받은 모델은 같은 답을 다시 낸다."""
    builder = forecast_builder(queried=set())
    draft = builder.parse(
        answer(reasons=[{"factor": "WTI", "direction": "up", "statement": "안 부른 요인"}])
    )
    reason = builder._needs_repair(draft)
    assert reason is not None
    assert "조회하지 않은 요인(WTI)" in reason


def test_a_big_move_with_no_observations_is_asked_again_once():
    """**설계 §6.2가 약속한 것이다.** 크게 움직인 날 관찰 0건이면 한 번 되묻는다."""
    from modules.kospi.toolbox import KospiToolbox

    empty = review_answer()
    filled = review_answer(
        observations=[{"factor": "US10Y", "sign": "inverse", "strength": 2, "note": "금리 상승"}]
    )
    model = _ScriptedModel(empty, filled)
    toolbox = KospiToolbox(_RecordingConnection(), as_of_at=datetime(2026, 9, 2, 10, 0, tzinfo=UTC))
    # 관찰이 살아남으려면 그 요인을 이 대화에서 조회했어야 한다.
    toolbox.run("factor_history", {"factor": "US10Y", "days": 3})
    observed = ReviewState(
        run_date=date(2026, 9, 2),
        as_of_kst="2026-09-02 19:00 KST",
        close=Decimal("6562.72"),
        previous_close=Decimal("6835.80"),
        change_pct=Decimal("-3.99"),
    )

    draft = ReviewBuilder(model, toolbox, observed=observed).build()

    assert model.calls == 2
    assert [item.factor for item in draft.observations] == [Factor.US10Y]


def test_a_quiet_day_with_no_observations_is_not_asked_again():
    from modules.kospi.toolbox import KospiToolbox

    model = _ScriptedModel(review_answer())
    toolbox = KospiToolbox(_RecordingConnection(), as_of_at=datetime(2026, 9, 2, 10, 0, tzinfo=UTC))
    observed = ReviewState(
        run_date=date(2026, 9, 2),
        as_of_kst="2026-09-02 19:00 KST",
        close=Decimal("6840.00"),
        previous_close=Decimal("6835.80"),
        change_pct=Decimal("0.06"),
    )

    draft = ReviewBuilder(model, toolbox, observed=observed).build()

    assert model.calls == 1
    assert draft.observations == ()


# --- 발송 스위치 --------------------------------------------------------------


def test_slack_is_on_by_default():
    """**조용한 기본값을 만들지 않는다.** Param이 없으면 보낸다."""
    from modules.kospi import common

    assert common.notify_enabled({}) is True
    assert common.notify_enabled({"params": {}}) is True
    assert common.notify_enabled({"params": {"notify": None}}) is True


def test_slack_can_be_turned_off_for_a_backfill():
    """관찰 20영업일 백필이 운영 채널에 스무 번 나가는 것을 막는 자리다."""
    from modules.kospi import common

    assert common.notify_enabled({"params": {"notify": False}}) is False


def test_every_kospi_dag_exposes_the_notify_switch():
    """DAG 하나가 빠지면 그 DAG의 백필이 채널을 도배한다."""
    import re
    from pathlib import Path

    dags = Path(__file__).resolve().parents[2] / "airflow" / "dags"
    for name in ("kospi_forecast_daily.py", "kospi_intraday_daily.py", "kospi_review_daily.py"):
        source = (dags / name).read_text(encoding="utf-8")
        assert "common.notify_param()" in source, name
        # docstring의 params 표에도 적혀 있어야 한다 — 운영자가 UI 전에 읽는 자리다.
        assert re.search(r"^\| `notify` \|", source, re.MULTILINE), name


# --- 드라이버 경계의 타입 ------------------------------------------------------


class _CapturingSession:
    """`session.run`에 실린 파라미터를 그대로 모으는 가짜. 결과는 언제나 비어 있다."""

    def __init__(self, captured: dict[str, object]) -> None:
        self.captured = captured

    def run(self, statement: str, **parameters: object):
        self.captured.update(parameters)
        return _CapturingSession._EmptyResult()

    def begin_transaction(self):
        return self

    def commit(self) -> None:
        return None

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> bool:
        return False

    class _EmptyResult:
        def __iter__(self):
            return iter(())

        def single(self):
            return None


class _CapturingDriver:
    def __init__(self, captured: dict[str, object]) -> None:
        self.captured = captured

    def session(self):
        return _CapturingSession(self.captured)


def test_dates_reach_the_driver_as_plain_types_not_pendulum():
    """**드라이버는 정확한 타입으로 직렬화 훅을 고른다 — 서브클래스면 죽는다.**

    2026-09-03 첫 운영 실행이 여기서 죽었다:
    `ValueError: Values of type <class 'pendulum.date.Date'> are not supported`.
    `datetime`만 벗기고 `date`를 안 벗긴 것이 원인이다. pendulum `DateTime.date()`가
    pendulum `Date`를 주므로 KST 날짜를 뽑는 경로가 전부 그 타입을 만든다.

    조회와 쓰기를 함께 잠근다 — 쓰기 쪽은 `run_date` 하나를 다섯 쿼리가 나눠 쓴다.
    """
    import pendulum

    from modules.kospi import graph as kospi_graph

    moment = pendulum.datetime(2026, 9, 3, 8, 35, tz="Asia/Seoul")
    pendulum_date = moment.date()
    # 전제가 깨지면(pendulum이 표준 타입을 주게 되면) 이 테스트는 아무것도 안 지킨다.
    assert type(pendulum_date) is not date

    read: dict[str, object] = {}
    kospi_graph.read_memories(_CapturingDriver(read), as_of_date=pendulum_date, as_of_at=moment)
    assert read, "조회가 파라미터를 하나도 안 실었다"

    written: dict[str, object] = {}
    kospi_graph.write_review(
        _CapturingDriver(written),
        run_date=pendulum_date,
        observations=(),
        new_memories=(),
        kept_ids=(1,),
        retired=(),
        unreviewed_ids=(),
        llm_run_id=None,
        created_at=moment,
    )
    assert written, "쓰기가 파라미터를 하나도 안 실었다"

    for where, captured in (("조회", read), ("쓰기", written)):
        for name, value in captured.items():
            if isinstance(value, datetime):
                assert type(value) is datetime, f"{where}의 {name}이 {type(value)}"
            elif isinstance(value, date):
                assert type(value) is date, f"{where}의 {name}이 {type(value)}"
