"""장후 관찰 — 채점, 관계 관찰, 메모의 수명.

태스크 셋이 여기 있다.

| 태스크 | LLM | 무엇 |
| --- | --- | --- |
| `grade_forecast` | 없다 | 오늘 종가로 오늘 슬롯들을 채점한다. 순수 함수가 판정한다 |
| `observe_relations` | 있다 | 오늘 무엇이 움직였나 + 새 메모 + 메모 판정. 그래프에 쓴다 |
| `notify_slack` | 없다 | 채점과 관찰을 보낸다 |

**채점을 관찰과 나눈 이유는 실패의 성격이 다르기 때문이다.** 채점은 SQL이라 종가만 있으면
언제나 되고, 관찰은 모델을 부른다. 한 태스크로 묶으면 모델이 죽은 날 채점도 안 남는다.

## 메모의 수명은 여기서 정해진다

모델이 정하는 것은 `keep`/`drop` 하나뿐이다. 나머지 셋은 코드가 상한으로 정한다.

- **나이 상한** — `MEMORY_MAX_AGE_DAYS`를 넘으면 판정과 무관하게 내린다. 메모는 "요즘 볼
  것"이지 규칙이 아니다. 규칙이 되려면 관계 엣지로 쌓여 가중치가 되어야 한다.
- **미검토** — 답에서 빠진 메모는 `keep`으로 치지 않는다. 두 번 연속이면 내린다.
- **상한** — 활성이 `MAX_ACTIVE_MEMORIES`에 차 있으면 새 메모를 안 쓰고 센다.

이 셋이 "LLM 출력이 LLM 입력이 되어 스스로를 강화하는" 순환을 끊는 자리다.
"""

import logging
from collections.abc import Sequence
from datetime import date
from typing import Any

from airflow.exceptions import AirflowSkipException
from airflow.sdk import get_current_context

from modules import llm
from modules.kospi import common
from modules.kospi.domain import (
    MAX_ACTIVE_MEMORIES,
    MAX_UNREVIEWED,
    OBSERVATION_REQUIRED_PCT,
    REVIEW_PROMPT_VERSION,
    KospiError,
    MemoryVerdict,
    RetireReason,
    change_pct,
    factor_label,
    grade_forecast,
    memory_expired,
    memory_key,
)
from modules.kospi.generation import ReviewBuilder, ReviewDraft
from modules.kospi.graph import (
    GraphWriteResult,
    NewMemory,
    ObservationWrite,
    RetiredMemory,
    StoredMemory,
    ensure_schema,
    read_memories,
    write_review,
)
from modules.kospi.state import GradedForecast, ReviewState
from modules.kospi.store import KospiStore
from modules.kospi.toolbox import KospiToolbox

logger = logging.getLogger(__name__)

LLM_RUN_KIND = "review"


# ---------------------------------------------------------------------------
# ① 채점 — LLM이 없다
# ---------------------------------------------------------------------------


def grade() -> dict[str, Any]:
    """미채점 전망을 오늘 종가로 채점한다.

    **날짜 상한이 없다** — 이 DAG가 며칠 죽어 있었으면 그 사이 전망도 여기서 회수된다.
    종가가 없는 날은 건너뛰고 사유를 남긴다. 0으로 꾸미지 않는다.
    """
    context = get_current_context()
    run_date = common.resolve_run_date(context)

    graded: list[str] = []
    skipped: list[str] = []
    with common.connection() as connection:
        store = KospiStore(connection)
        closes: dict[date, Any] = {}
        for pending in store.pending_grades(run_date=run_date):
            if pending.run_date not in closes:
                closes[pending.run_date] = store.session_close(pending.run_date)
            close = closes[pending.run_date]
            if close is None:
                skipped.append(f"{pending.run_date}/{pending.slot.value}(종가 없음)")
                continue
            result = grade_forecast(
                direction=pending.direction,
                expected_change_pct=pending.expected_change_pct,
                band_pct=pending.band_pct,
                base_price=pending.base_price,
                close_price=close,
            )
            written = store.store_grade(
                run_date=pending.run_date,
                slot=pending.slot,
                actual_change_pct=result.actual_change_pct,
                hit=result.hit,
                within_band=result.within_band,
            )
            if written:
                graded.append(f"{pending.run_date}/{pending.slot.value}")
            else:
                skipped.append(f"{pending.run_date}/{pending.slot.value}(이미 채점됨)")

    if skipped:
        logger.warning("채점하지 못한 전망 %s건: %s", len(skipped), "; ".join(skipped))
    logger.info("채점 %s건", len(graded))
    return {"run_date": run_date.isoformat(), "graded": len(graded), "skipped": len(skipped)}


# ---------------------------------------------------------------------------
# ② 관찰 — LLM이 있다
# ---------------------------------------------------------------------------


def observe() -> dict[str, Any]:
    """오늘 무엇이 움직였는지를 관찰해 그래프에 쓴다.

    **휴장일은 skip이다.** 오늘 종가가 없으면 시장이 안 열린 것이거나 수집이 아직 안 된
    것인데, 후자는 `kis_index_daily`(18:20)가 끝난 19:00 실행에서 드물다. 둘을 가르지 않고
    skip으로 두는 이유는 어느 쪽이든 관찰할 것이 없기 때문이다.
    """
    context = get_current_context()
    run_date = common.resolve_run_date(context)
    as_of_at = common.review_at(run_date)

    with common.connection() as connection, common.graph() as graph:
        store = KospiStore(connection)
        close = store.session_close(run_date)
        if close is None:
            raise AirflowSkipException(f"{run_date} 확정 종가가 없다. 휴장이거나 수집 전이다")

        previous = store.previous_close(as_of_at=as_of_at, before_date=run_date)
        if previous is None:
            raise KospiError(f"{run_date} 앞의 확정 종가가 없어 등락률을 낼 수 없다")
        change = change_pct(previous[1], close)

        ensure_schema(graph)
        memories = read_memories(graph, as_of_date=run_date, as_of_at=as_of_at)
        observed = ReviewState(
            run_date=run_date,
            as_of_kst=common.label(as_of_at),
            close=close,
            previous_close=previous[1],
            change_pct=change,
            bars=store.bars(as_of_at=as_of_at, before_date=_next_day(run_date)),
            relations=common.relation_rows(graph, as_of_date=run_date, as_of_at=as_of_at),
            memories=common.memory_rows(graph, as_of_date=run_date, as_of_at=as_of_at),
            forecasts=_graded_forecasts(store, run_date),
        )

        model = llm.kospi_model(common.conversation_id(run_date, "review"))
        toolbox = KospiToolbox(connection, as_of_at=as_of_at)
        builder = ReviewBuilder(model, toolbox, observed=observed)

        llm_run_id = store.start_llm_run(
            kind=LLM_RUN_KIND,
            run_date=run_date,
            slot=None,
            as_of_at=as_of_at,
            llm_model=llm.model_name(model),
            prompt_version=REVIEW_PROMPT_VERSION,
            dag_run_id=_dag_run_id(context),
            try_number=_try_number(context),
        )

        try:
            draft = builder.build()
            _require_observations(draft, change=change)
            plan = plan_memories(draft, active=memories, as_of_date=run_date)
            written = write_review(
                graph,
                run_date=run_date,
                observations=[
                    ObservationWrite(
                        factor=item.factor, sign=item.sign, strength=item.strength, note=item.note
                    )
                    for item in draft.observations
                ],
                new_memories=plan["new"],
                kept_ids=plan["kept"],
                retired=plan["retired"],
                unreviewed_ids=plan["unreviewed"],
                llm_run_id=llm_run_id,
                # **벽시계가 아니라 기준 시각이다.** 재시도가 20분 뒤에 돌아도 그 관찰이
                # 본 것은 `as_of_at`까지다. 백필은 몇 달 뒤에 돌 수도 있는데, 그때 벽시계를
                # 쓰면 그 엣지가 "오늘 알게 된 것"이 되어 과거 전망이 영영 못 본다.
                created_at=as_of_at,
            )
        # 넓게 잡되 **반드시 다시 올린다.** 여기서 잡는 이유는 원장을 닫는 것 하나뿐이다.
        except BaseException as error:
            toolbox.close_open_records()
            store.finish_llm_run(
                llm_run_id,
                status="failed",
                records=toolbox.tool_calls,
                tool_rounds=toolbox.round_count,
                truncated=False,
                rejected=0,
                usage=builder.usage,
                error=f"{type(error).__name__}: {error}",
            )
            raise

        toolbox.close_open_records()
        store.finish_llm_run(
            llm_run_id,
            status="succeeded",
            records=toolbox.tool_calls,
            tool_rounds=draft.tool_rounds,
            truncated=draft.truncated,
            rejected=draft.rejected,
            memories={
                "written": written.memories_written,
                "rejected": draft.memories_rejected + plan["rejected"],
                "kept": written.memories_kept,
                "dropped": written.memories_dropped,
                "unreviewed": written.memories_unreviewed,
                "expired": written.memories_expired,
            },
            usage=builder.usage,
        )

    return _observe_result(run_date, change=change, close=close, draft=draft, plan=plan, written=written)


def _require_observations(draft: ReviewDraft, *, change: Any) -> None:
    """**조용한 성공을 만들지 않는다.**

    크게 움직인 날에 관찰이 0건이면 그것은 답이 아니다. 교정은 그래프가 이미 한 번 했으므로
    여기까지 0건으로 오면 태스크를 죽인다. 작게 움직인 날의 0건은 정상이고 원장에 남는다.
    """
    if draft.observations:
        return
    if abs(change) >= OBSERVATION_REQUIRED_PCT:
        raise KospiError(
            f"코스피가 {change}퍼센트 움직였는데 관찰이 0건이다(버린 것 {draft.rejected}건)"
        )
    logger.info("등락 %s퍼센트로 조용한 날이라 관찰 0건을 받아들인다", change)


def plan_memories(
    draft: ReviewDraft,
    *,
    active: Sequence[StoredMemory],
    as_of_date: date,
) -> dict[str, Any]:
    """메모의 수명을 정한다. **순수 함수다** — 그래프도 DB도 안 본다.

    모델이 정하는 것은 `keep`/`drop` 하나뿐이고 나머지 셋(만료·미검토·상한)은 여기가 정한다.
    경계값을 DB 없이 테스트할 수 있어야 해서 이 판정이 `observe()` 밖에 산다.
    """
    verdicts = {item.id: item for item in draft.reviews}
    retired: list[RetiredMemory] = []
    kept: list[int] = []
    unreviewed: list[int] = []

    for memory in active:
        if memory_expired(memory.created_on, as_of_date):
            # **나이 상한이 모델 판정보다 앞선다.** 모델이 `keep`이라 해도 내린다.
            retired.append(
                RetiredMemory(id=memory.id, reason=RetireReason.EXPIRED, note=f"작성 {memory.created_on}")
            )
            continue
        review = verdicts.get(memory.id)
        if review is None:
            if memory.unreviewed_count + 1 >= MAX_UNREVIEWED:
                retired.append(
                    RetiredMemory(id=memory.id, reason=RetireReason.UNREVIEWED, note="두 번 연속 검토에서 빠졌다")
                )
            else:
                unreviewed.append(memory.id)
            continue
        if review.verdict is MemoryVerdict.DROP:
            retired.append(RetiredMemory(id=memory.id, reason=RetireReason.DROPPED, note=review.reason))
        else:
            kept.append(memory.id)

    # 상한은 **이번에 내리는 것을 뺀 뒤** 센다. 지운 자리에 새로 쓰는 것이 정상 흐름이다.
    retired_ids = {item.id for item in retired}
    remaining = sum(1 for memory in active if memory.id not in retired_ids)
    existing_keys = {memory_key(memory.text) for memory in active if memory.id not in retired_ids}

    new: list[NewMemory] = []
    rejected = 0
    for item in draft.memories:
        key = memory_key(item.text)
        if key in existing_keys:
            rejected += 1
            continue
        if remaining + len(new) >= MAX_ACTIVE_MEMORIES:
            rejected += 1
            continue
        existing_keys.add(key)
        new.append(NewMemory(text=item.text, reason=item.reason, factor=item.factor))

    if rejected:
        logger.warning("메모 %s건을 쓰지 않았다(중복 또는 상한 %s)", rejected, MAX_ACTIVE_MEMORIES)
    return {"new": new, "kept": kept, "retired": retired, "unreviewed": unreviewed, "rejected": rejected}


def _graded_forecasts(store: KospiStore, run_date: date) -> tuple[GradedForecast, ...]:
    """오늘 슬롯들의 전망과 채점. 관찰이 "무엇을 예측했고 어땠나"를 함께 본다."""
    return tuple(
        GradedForecast(
            slot=row.slot,
            direction=row.direction,
            expected_change_pct=row.expected_change_pct,
            band_pct=row.band_pct,
            base_price=row.base_price,
            reasons=tuple(str(item.get("statement", "")) for item in row.reasons),
            actual_change_pct=row.actual_change_pct,
            hit=row.hit,
            within_band=row.within_band,
        )
        for row in store.forecasts(run_date)
    )


def _observe_result(
    run_date: date,
    *,
    change: Any,
    close: Any,
    draft: ReviewDraft,
    plan: dict[str, Any],
    written: GraphWriteResult,
) -> dict[str, Any]:
    """XCom을 지나는 값. Slack이 이것과 DB의 채점 줄을 합쳐 메시지를 만든다.

    관찰 문장을 여기 싣는 이유는 그래프를 다시 읽지 않기 위해서다 — 하루 한 번 쓰는 값이라
    그 사이에 바뀌지 않는다.
    """
    return {
        "run_date": run_date.isoformat(),
        "change_pct": str(change),
        "close": str(close),
        "prompt_version": REVIEW_PROMPT_VERSION,
        "observations": [
            {
                "factor": item.factor.value,
                "label": factor_label(item.factor),
                "sign": item.sign.value,
                "strength": item.strength,
                "note": item.note,
            }
            for item in draft.observations
        ],
        "new_memories": [item.text for item in plan["new"]],
        "memories_written": written.memories_written,
        "memories_kept": written.memories_kept,
        "memories_dropped": written.memories_dropped,
        "memories_expired": written.memories_expired,
        "memories_unreviewed": written.memories_unreviewed,
    }


def _next_day(day: date) -> date:
    return date.fromordinal(day.toordinal() + 1)


def _dag_run_id(context: dict[str, Any]) -> str:
    run = context.get("dag_run")
    return str(getattr(run, "run_id", "") or "unknown")


def _try_number(context: dict[str, Any]) -> int:
    instance = context.get("task_instance") or context.get("ti")
    return int(getattr(instance, "try_number", 1) or 1)
