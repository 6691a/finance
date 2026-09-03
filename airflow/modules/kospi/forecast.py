"""장전 전망 슬롯(`pre_open`).

**이 모듈이 아는 것은 셋이다** — 기준가가 직전 거래일 확정 종가라는 것, 준비 검사가 일봉을
기다린다는 것, 기준 시각이 08:35라는 것. 나머지는 `common`과 `intraday`가 같은 것을 쓴다.

장중과 나눈 이유는 앞단이 다르기 때문이다. 장전은 밤사이 매크로 수집(07:30~08:50)과 문서
평가를 기다리고, 장중은 분봉과 수급 스냅샷을 기다린다 — 기다리는 것이 다르면 실패의 성격도
다르다.
"""

import logging
from typing import Any

from airflow.sdk import get_current_context

from modules.kospi import common
from modules.kospi.domain import KospiNotReady, RunSlot
from modules.kospi.run import build_and_store

logger = logging.getLogger(__name__)

SLOT = RunSlot.PRE_OPEN


def build() -> Any:
    """장전 전망 하나를 만들고 저장한다. DAG 태스크가 부르는 유일한 함수다."""
    context = get_current_context()
    run_date = common.resolve_run_date(context)
    as_of_at = common.slot_at(run_date, SLOT)

    with common.connection() as connection, common.graph() as graph:
        return build_and_store(
            connection=connection,
            graph=graph,
            run_date=run_date,
            slot=SLOT,
            as_of_at=as_of_at,
            context=context,
            resolve_base=_base,
        )


def _base(store: Any, *, run_date: Any, as_of_at: Any) -> dict[str, Any]:
    """장전의 기준가 — **직전 거래일 확정 종가.**

    전일이 아니라 "앞의 마지막"이다. 연휴 뒤 첫 거래일은 사흘 전 종가가 기준가이고 그것이
    맞는 축이다 — 그 사이에 KRX 정규장 거래가 없었다.

    없으면 준비 검사 실패다. `kis_index_daily`가 아직 안 돌았거나 밀린 것이라 재시도로 풀린다.
    """
    found = store.previous_close(as_of_at=as_of_at, before_date=run_date)
    if found is None:
        raise KospiNotReady(f"{run_date} 앞의 확정 종가가 없다. kis_index_daily를 기다린다")
    business_date, close = found
    return {
        "base_price": close,
        "base_at": common.close_at(business_date),
        "base_note": f"직전 거래일({business_date}) KRX 정규장 확정 종가",
        "so_far_pct": None,
        "intraday": None,
    }
