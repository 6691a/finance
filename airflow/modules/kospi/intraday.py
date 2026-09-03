"""장중·마감전 전망 슬롯(`midday`·`pre_close`).

**슬롯을 시계로 정하지 않는다.** `resolve_slot`이 ① Param → ② `logical_date`의 시각이 슬롯
표와 **정확히** 일치 → ③ 실패 순으로 정한다. 가까운 슬롯으로 반올림하지 않는다.

그 규칙이 있는 이유는 실측이다. 옛 추론은 한 DAG가 여러 시각에 돌며 `logical_date`의 시각으로
슬롯을 갈랐고, `logical_date`가 없는 수동 실행이 벽시계로 떨어져 **UI의 Trigger 버튼이 조용히
다른 슬롯을 돌렸다**(2026-08-21). 조용히 다른 슬롯을 도는 것보다 안 도는 편이 낫다.

기준가가 장전과 다르다 — **그 시각 현재가**이고, 답은 거기서 마감까지다. 그 판정과 준비 검사는
`run.intraday_base`가 갖는다.
"""

import logging
from typing import Any

from airflow.exceptions import AirflowFailException
from airflow.sdk import Param, get_current_context

from modules.kospi import common
from modules.kospi.domain import BAR_STALENESS, INTRADAY_SLOTS, SLOT_LABELS, SLOT_TIMES, RunSlot
from modules.kospi.run import build_and_store, intraday_base
from modules.utility import KST_TIMEZONE

logger = logging.getLogger(__name__)

RUN_SLOT_PARAM = "run_slot"


def run_slot_param() -> dict[str, Param]:
    """장중 DAG만 갖는 Param. **수동 실행은 반드시 고른다.**"""
    return {
        RUN_SLOT_PARAM: Param(
            None,
            type=["null", "string"],
            enum=[None, *[slot.value for slot in INTRADAY_SLOTS]],
            title="장중 슬롯",
            description=(
                "비우면 스케줄된 시각으로 정한다. 시각이 슬롯 표와 정확히 일치하지 않으면 실행이 죽는다 — "
                "수동 실행은 여기서 고른다."
            ),
        ),
    }


def resolve_slot(context: Any) -> RunSlot:
    """이 실행의 슬롯. **가까운 값으로 반올림하지 않는다.**

    Param이 있으면 그것이 답이다. 없으면 `logical_date`의 KST 시·분이 슬롯 표와 정확히
    같아야 하고, 아니면 죽인다.
    """
    given = (context.get("params") or {}).get(RUN_SLOT_PARAM)
    if given:
        try:
            slot = RunSlot(str(given).strip())
        except ValueError as error:
            raise AirflowFailException(
                f"{RUN_SLOT_PARAM} must be one of {[item.value for item in INTRADAY_SLOTS]}, got {given!r}"
            ) from error
        if slot not in INTRADAY_SLOTS:
            raise AirflowFailException(f"{slot.value}는 장중 슬롯이 아니다")
        return slot

    logical = context.get("logical_date")
    if logical is None:
        raise AirflowFailException(
            f"logical_date가 없다. 수동 실행은 {RUN_SLOT_PARAM}을 반드시 고른다"
        )
    moment = logical.astimezone(KST_TIMEZONE).time().replace(second=0, microsecond=0)
    for slot in INTRADAY_SLOTS:
        if SLOT_TIMES[slot] == moment:
            return slot
    raise AirflowFailException(
        f"{moment}는 슬롯 표에 없다. cron과 kospi.domain.SLOT_TIMES가 어긋났는지 본다"
    )


def build() -> Any:
    """장중 전망 하나를 만들고 저장한다. DAG 태스크가 부르는 유일한 함수다."""
    context = get_current_context()
    run_date = common.resolve_run_date(context)
    slot = resolve_slot(context)
    as_of_at = common.slot_at(run_date, slot)
    logger.info("%s 슬롯(%s)으로 돈다", SLOT_LABELS[slot], slot.value)

    with common.connection() as connection, common.graph() as graph:
        return build_and_store(
            connection=connection,
            graph=graph,
            run_date=run_date,
            slot=slot,
            as_of_at=as_of_at,
            context=context,
            resolve_base=lambda store, **kwargs: intraday_base(store, staleness=BAR_STALENESS, **kwargs),
        )
