"""웹 테스트가 함께 쓰는 행 픽스처와 컨테이너.

**실 DB를 띄우지 않는다**(프로젝트 관례). 가짜는 리포지토리 자리에 끼우고, 끼우는 방법은
`container.forecast_repository.override(...)`다 — FastAPI `dependency_overrides`가 아니라
컨테이너 provider를 바꾸는 것이 `dependency_injector`의 문서화된 형태다.
"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from apps.api.container import ApiContainer
from apps.core.database import DatabaseConfig
from apps.models.analysis import (
    KospiDirection,
    KospiForecast,
    KospiLlmRun,
    KospiLlmRunKind,
    KospiLlmRunStatus,
    KospiSlot,
    KospiToolCall,
    KospiToolCallErrorKind,
)

AS_OF = datetime(2026, 9, 3, 2, 35, tzinfo=UTC)
FINISHED = datetime(2026, 9, 3, 2, 36, 30, tzinfo=UTC)
RUN_DATE = date(2026, 9, 3)


def databases() -> dict[str, DatabaseConfig]:
    """`Database.__init__`이 runtime-enabled `default`를 요구한다. 그래서 별칭 전체를 넘긴다 —
    read_only 하나만 걸러 넘길 수 없다. 쓰기 가능한 `default` 엔진 객체가 만들어지지만
    SQLAlchemy 엔진은 lazy라 커넥션이 안 열리고 코드가 그 별칭을 절대 안 집는다."""
    return {
        "default": DatabaseConfig(url="postgresql+asyncpg://x/y"),
        "prod": DatabaseConfig(url="postgresql+asyncpg://x/y", read_only=True),
    }


def container(alias: str = "prod") -> ApiContainer:
    """엔진은 lazy라 접속하지 않는다. 설정을 밖에서 받는 덕에 config.yaml도 필요 없다."""
    return ApiContainer(settings=SimpleNamespace(databases=databases()), db_alias=alias)


def forecast_row(
    forecast_id: int = 1,
    slot: KospiSlot = KospiSlot.MIDDAY,
    graded: bool = False,
    llm_run_id: int | None = 9,
) -> KospiForecast:
    """전망 한 행.

    **채점 칸 넷은 함께 있거나 함께 없다**(DB CHECK). 그래서 `graded` 하나로 넷을 켠다.
    `base_at`은 `as_of_at`과 일부러 다르다 — 장중 슬롯은 기준 시각 직전 봉을 본다.
    """
    return KospiForecast(
        id=forecast_id,
        run_date=RUN_DATE,
        slot=slot,
        as_of_at=AS_OF,
        base_price=Decimal("6652.7500"),
        base_at=AS_OF - timedelta(minutes=1),
        so_far_pct=None if slot is KospiSlot.PRE_OPEN else Decimal("1.37"),
        direction=KospiDirection.UP,
        expected_change_pct=Decimal("0.60"),
        band_pct=Decimal("1.40"),
        reasons=[
            {
                "factor": "FOREIGN_NET_BUY",
                "memory_id": None,
                "slot_ref": None,
                "direction": "up",
                "statement": "외국인이 3일 연속 순매수다",
            },
            {
                "factor": None,
                "memory_id": 17,
                "slot_ref": None,
                "direction": "down",
                "statement": "목요일 CPI 앞두고 관망",
            },
        ],
        input_state={"run_date": "2026-09-03", "bars": []},
        weak=False,
        rejected_reasons=0,
        actual_change_pct=Decimal("-2.31") if graded else None,
        hit=False if graded else None,
        within_band=False if graded else None,
        graded_at=FINISHED if graded else None,
        prompt_version="3",
        llm_model="grok-4.6",
        dag_run_id="scheduled__2026-09-03T02:35:00+00:00",
        llm_run_id=llm_run_id,
    )


def llm_run_row(
    run_id: int = 9,
    kind: KospiLlmRunKind = KospiLlmRunKind.FORECAST,
    status: KospiLlmRunStatus = KospiLlmRunStatus.SUCCEEDED,
) -> KospiLlmRun:
    """**`running`은 종료 시각과 사유가 둘 다 비어 있다**(DB CHECK가 그 조합만 허용한다).

    메모 칸 일곱은 `review`에만 값이 있다 — 전망 대화에 0을 넣으면 "0건"과 "해당 없음"이
    같아 보인다.
    """
    review = kind is KospiLlmRunKind.REVIEW
    return KospiLlmRun(
        id=run_id,
        kind=kind,
        run_date=RUN_DATE,
        slot=None if review else KospiSlot.MIDDAY,
        as_of_at=AS_OF,
        dag_run_id="scheduled__x",
        try_number=1,
        llm_model="grok-4.6",
        prompt_version="1" if review else "3",
        started_at=AS_OF,
        finished_at=None if status is KospiLlmRunStatus.RUNNING else FINISHED,
        status=status,
        error="모델이 붙지 않았다" if status is KospiLlmRunStatus.FAILED else None,
        tool_rounds=2,
        tool_calls=11,
        tool_result_chars=54555,
        truncated=False,
        rejected=0,
        observations_written=4 if review else None,
        memories_written=1 if review else None,
        memories_rejected=0 if review else None,
        memories_kept=2 if review else None,
        memories_dropped=1 if review else None,
        memories_unreviewed=0 if review else None,
        memories_expired=0 if review else None,
        prompt_tokens=12000,
        cached_tokens=8000,
        completion_tokens=900,
        reasoning_tokens=400,
    )


def tool_call_row(
    seq: int = 1,
    run_id: int = 9,
    round_no: int = 1,
    failed: bool = False,
    delivered: bool = True,
) -> KospiToolCall:
    """성공과 실패는 배타다 — `result`와 `error` 둘 중 하나만 채운다(DB CHECK)."""
    return KospiToolCall(
        llm_run_id=run_id,
        seq=seq,
        round_no=round_no,
        tool_call_id=f"call_{seq}",
        tool_name="factor_history",
        arguments={"factor": "US10Y", "days": 10},
        validated_arguments=None if failed else {"factor": "US10Y", "days": 10},
        requested_at=AS_OF,
        duration_ms=None if failed else 42,
        result_chars=0 if failed else 18,
        result=None if failed else '{"rows": []}',
        delivered=delivered,
        error_kind=KospiToolCallErrorKind.VALIDATION if failed else None,
        error="days must be <= 30" if failed else None,
    )
