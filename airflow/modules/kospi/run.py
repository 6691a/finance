"""전망 하나를 만들고 저장하는 순서. **슬롯 셋이 이 함수 하나를 지난다.**

슬롯마다 다른 것은 `resolve_base` 하나뿐이다 — 기준가를 어디서 읽나. 그것을 인자로 받으니
이 모듈에 `if slot == ...`이 없다.

## 원장이 그래프 밖에 있는 이유

모델을 부르기 전에 `running` 행을 커밋하고 어떻게 끝나든 닫는다. 그래프 안에 두면 그래프가
죽었을 때 닫는 코드에 도달하지 못한다 — 그러면 "안 돌았다"와 "돌다 죽었다"가 같아 보인다.

## 첫 성공본은 불변이다

같은 `(run_date, slot)`에 행이 있으면 모델을 아예 부르지 않는다. 재실행은 기존 행을 읽어
발송으로 넘길 뿐이다.
"""

import logging
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from modules.kospi import common
from modules.kospi.domain import PROMPT_VERSION, RunSlot, change_pct
from modules.kospi.store import KospiStore

logger = logging.getLogger(__name__)

LLM_RUN_KIND = "forecast"


def build_and_store(
    *,
    connection: Any,
    graph: Any,
    run_date: date,
    slot: RunSlot,
    as_of_at: datetime,
    context: dict[str, Any],
    resolve_base: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """전망 하나를 만들고 저장한다. 결과는 XCom을 지날 dict다.

    Airflow가 Pydantic 모델을 어떻게 직렬화하는지에 기대지 않으려고 dict로 나간다.
    """
    # **무거운 것은 여기서 올린다.** LangChain은 첫 import에 몇 초를 쓰는데, DagBag은 모든
    # DAG 파일을 주기적으로 다시 파싱하면서 태스크는 돌리지 않는다. 모듈 수준에 두면
    # 전망을 만들지도 않는 파싱이 매번 그 무게를 문다(2026-09-03 실측 202개 모듈).
    from modules import llm
    from modules.kospi.generation import ForecastBuilder
    from modules.kospi.toolbox import KospiToolbox

    store = KospiStore(connection)

    existing = {row.slot: row for row in store.forecasts(run_date)}
    if slot in existing:
        logger.info("%s %s에 이미 전망이 있다. 모델을 부르지 않는다", run_date, slot.value)
        return _result(run_date, slot, reused=True)

    base = resolve_base(store, run_date=run_date, as_of_at=as_of_at)
    observed = common.build_observed_state(
        store=store,
        graph_handle=graph,
        run_date=run_date,
        slot=slot,
        as_of_at=as_of_at,
        base_price=base["base_price"],
        base_at=base["base_at"],
        base_note=base["base_note"],
        intraday=base["intraday"],
    )

    model = llm.kospi_model(common.conversation_id(run_date, slot))
    toolbox = KospiToolbox(connection, as_of_at=as_of_at)
    builder = ForecastBuilder(model, toolbox, observed=observed)

    llm_run_id = store.start_llm_run(
        kind=LLM_RUN_KIND,
        run_date=run_date,
        slot=slot,
        as_of_at=as_of_at,
        llm_model=llm.model_name(model),
        prompt_version=PROMPT_VERSION,
        dag_run_id=_dag_run_id(context),
        try_number=_try_number(context),
    )

    try:
        draft = builder.build()
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
        usage=builder.usage,
    )

    if draft.weak:
        # **조용한 성공을 만들지 않는다.** 근거 0건은 태스크 실패가 아니라 라벨이다 —
        # 방향·크기는 그 자체로 채점되는 값이라 버리면 그날 표본이 사라진다.
        logger.warning(
            "%s %s 전망이 근거 없이 저장된다(버린 이유 %s건)", run_date, slot.value, draft.rejected
        )

    stored_id = store.store_forecast(
        run_date=run_date,
        slot=slot,
        as_of_at=as_of_at,
        base_price=observed.base_price,
        base_at=base["base_at"],
        so_far_pct=base["so_far_pct"],
        direction=draft.direction,
        expected_change_pct=draft.expected_change_pct,
        band_pct=draft.band_pct,
        reasons=[reason.model_dump(mode="json") for reason in draft.reasons],
        weak=draft.weak,
        rejected_reasons=draft.rejected,
        input_state=observed.model_dump(mode="json"),
        prompt_version=PROMPT_VERSION,
        llm_model=llm.model_name(model),
        dag_run_id=_dag_run_id(context),
        llm_run_id=llm_run_id,
    )
    if stored_id is None:
        # 우리가 조회한 뒤 다른 실행이 먼저 썼다. 첫 성공본이 이긴다.
        logger.warning("%s %s 전망이 이미 있어 이번 답은 버린다", run_date, slot.value)
    return _result(run_date, slot, reused=stored_id is None)


def intraday_base(
    store: KospiStore,
    *,
    run_date: date,
    as_of_at: datetime,
    staleness: Any,
) -> dict[str, Any]:
    """장중 슬롯의 기준가 — **그 시각 현재가.**

    준비 검사가 여기 있다. 최신 봉이 `staleness`보다 오래됐으면 실행하지 않는다 — 오래된
    가격을 "지금"으로 읽고 답하는 것보다 안 도는 편이 낫다.
    """
    from modules.kospi.domain import KospiNotReady
    from modules.kospi.state import FlowRow, IntradayState

    quote = store.intraday_quote(as_of_at=as_of_at, session_start=common.open_at(run_date))
    if quote is None:
        raise KospiNotReady(f"{run_date} 장중 분봉이 없다. kis_quote_intraday를 기다린다")
    age = as_of_at - quote.bar_at
    if age > staleness:
        raise KospiNotReady(f"최신 분봉이 {age}만큼 오래됐다({quote.bar_at.isoformat()}). 지금 값이 아니다")

    so_far = (
        change_pct(quote.previous_close, quote.close)
        if quote.previous_close and quote.previous_close > 0
        else None
    )
    flow = store.market_flow(as_of_at=as_of_at, session_start=common.open_at(run_date))
    return {
        "base_price": quote.close,
        "base_at": quote.bar_at,
        "base_note": f"{common.label(quote.bar_at)} 분봉 종가(현재가)",
        "so_far_pct": so_far,
        "intraday": IntradayState(
            open=quote.session_open,
            high=quote.session_high,
            low=quote.session_low,
            so_far_pct=so_far,
            flows=(
                FlowRow(
                    observed_at=common.label(flow.observed_at),
                    foreign_net_buy_qty=_as_float(flow.foreign_net_buy_qty),
                    institution_net_buy_qty=_as_float(flow.institution_net_buy_qty),
                    individual_net_buy_qty=_as_float(flow.individual_net_buy_qty),
                )
                if flow
                else None
            ),
        ),
    }


def _as_float(value: Any) -> float | None:
    """`None`은 그대로 둔다. 0으로 채우면 "재지 않았다"가 "0이다"가 된다."""
    return None if value is None else float(value)


def _result(run_date: date, slot: RunSlot, *, reused: bool) -> dict[str, Any]:
    """XCom을 지나는 값. 발송 태스크가 이것으로 DB를 다시 읽는다.

    **전망 내용을 XCom에 싣지 않는다.** 발송이 재시도되면 그 사이에 DB가 원본이고, 두 벌을
    들고 다니면 어느 쪽이 맞는지 정해야 한다.
    """
    return {"run_date": run_date.isoformat(), "slot": slot.value, "reused": reused}


def _dag_run_id(context: dict[str, Any]) -> str:
    run = context.get("dag_run")
    return str(getattr(run, "run_id", "") or "unknown")


def _try_number(context: dict[str, Any]) -> int:
    instance = context.get("task_instance") or context.get("ti")
    return int(getattr(instance, "try_number", 1) or 1)
