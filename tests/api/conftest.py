"""웹 테스트가 함께 쓰는 행 픽스처와 컨테이너.

**실 DB를 띄우지 않는다**(프로젝트 관례). 가짜는 리포지토리 자리에 끼우고, 끼우는 방법은
`container.thesis_repository.override(...)`다 — FastAPI `dependency_overrides`가 아니라
컨테이너 provider를 바꾸는 것이 `dependency_injector`의 문서화된 형태다.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

from apps.api.container import ApiContainer
from apps.core.database import DatabaseConfig
from apps.models.analysis import (
    LlmRunKind,
    LlmRunStatus,
    RunSlot,
    Thesis,
    ThesisDirection,
    ThesisEvidence,
    ThesisEvidenceKind,
    ThesisLlmRun,
    ThesisOutcome,
    ThesisSubjectKind,
    ThesisVerdict,
)

AS_OF = datetime(2026, 8, 26, 3, 35, tzinfo=UTC)
RUN_DATE = date(2026, 8, 26)


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


def thesis_row(thesis_id: int = 1, code: str = "KOSPI", llm_run_id: int | None = 9) -> Thesis:
    return Thesis(
        id=thesis_id,
        run_slot=RunSlot.INTRADAY_MIDDAY,
        run_date=RUN_DATE,
        as_of_at=AS_OF,
        dag_run_id="scheduled__2026-08-26T03:35:00+00:00",
        subject_kind=ThesisSubjectKind.INDEX,
        subject_code=code,
        label="코스피",
        prob_up=Decimal("0.3400"),
        prob_down=Decimal("0.4400"),
        prob_flat=Decimal("0.2200"),
        up_return_pct=Decimal("0.80"),
        down_return_pct=Decimal("1.20"),
        up_reasoning="오를 이유",
        down_reasoning="내릴 이유",
        flat_reasoning="횡보 이유",
        input_state={"session": "2026-08-25"},
        tool_rounds=2,
        llm_model="grok-4.6",
        prompt_version="7",
        llm_run_id=llm_run_id,
    )


def evidence_row(
    thesis_id: int = 1,
    horizon: int | None = None,
    rank: int = 1,
    ref: str = "document:4471",
) -> ThesisEvidence:
    return ThesisEvidence(
        thesis_id=thesis_id,
        outcome_horizon_days=horizon,
        evidence_kind=ThesisEvidenceKind.DOCUMENT,
        evidence_ref=ref,
        evidence_title="반도체 수출 증가",
        evidence_url="https://example.test/a",
        direction=None if horizon else ThesisDirection.DOWN,
        mechanism=None if horizon else "수급이 눌린다",
        detail={"value_score": 7},
        rank=rank,
    )


def outcome_row(horizon: int = 0, narration_run_id: int | None = None) -> ThesisOutcome:
    return ThesisOutcome(
        thesis_id=1,
        horizon_days=horizon,
        as_of_at=AS_OF,
        dag_run_id="scheduled__x",
        evaluated_at=AS_OF,
        actual_return_pct=Decimal("-1.5000"),
        actual_outcome=ThesisDirection.DOWN,
        brier_score=Decimal("0.51000"),
        predicted_return_pct=Decimal("1.20") if horizon == 0 else None,
        return_error_pct=Decimal("0.3000") if horizon == 0 else None,
        narrative="이래서 빠졌다" if horizon else None,
        verdict=ThesisVerdict.SUPPORTED if horizon else None,
        narrative_at=AS_OF if horizon else None,
        llm_model="grok-4.6" if horizon else None,
        prompt_version="2/informed" if horizon else None,
        narration_run_id=narration_run_id,
    )


def llm_run_row(run_id: int = 9, kind: LlmRunKind = LlmRunKind.FORECAST) -> ThesisLlmRun:
    return ThesisLlmRun(
        id=run_id,
        kind=kind,
        run_date=RUN_DATE,
        run_slot=RunSlot.INTRADAY_MIDDAY,
        horizon_days=None,
        as_of_at=AS_OF,
        dag_run_id="scheduled__x",
        try_number=1,
        llm_model="grok-4.6",
        prompt_version="7",
        started_at=AS_OF,
        finished_at=AS_OF,
        status=LlmRunStatus.SUCCEEDED,
        tool_rounds=2,
        tool_calls=11,
        tool_result_chars=54555,
    )
