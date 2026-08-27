"""주간 인과 그래프의 어휘와 셈 — 상수, 주 경계, 입력 해시.

**이 모듈은 LangChain·LangGraph·Airflow를 import하지 않는다.** DAG 테스트와 순수 함수
테스트가 그 무게 없이 돌아야 하고, `tests/modules/test_import_weight.py`가 그 경계를 잰다.
무거운 것은 `causal.generation`에 있다.

계약은 `docs/analysis/market-causal-graph.md`다.
"""

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from modules.utility import KST_TIMEZONE

# 프롬프트 판. `modules/prompts/causal_graph.yaml`의 문장을 고치면 이 값을 올리고
# `tests/modules/test_prompt_versions.py`의 해시를 같은 커밋에서 갱신한다.
PROMPT_VERSION = "1"

# 대상 주 `W`와 실행 주 `W+2`의 거리. 설계 §2.
RUN_LAG_WEEKS = 2

# event-time cutoff의 시각(KST). KRX 정규장 종가가 확정된 뒤다.
#
# **`W+1` 금요일이 KRX 휴장이어도 앞으로 당기지 않는다.** cutoff는 "이 시각 이후 감지된
# 행을 뺀다"라서, 휴장이면 그 앞 거래일까지의 값이 전부 이 시각 안에 들어와 있다. 반대로
# 마지막 거래일로 당기면 그 뒤에 들어온 문서를 잃는다.
CUTOFF_TIME_KST = time(15, 40)

# `date.fromisoformat`은 `2026-W28` 같은 ISO 주 표기도 받아 그 주의 월요일로 바꾼다.
# 조용히 통과하면 어느 표기로 준 실행인지 못 가르므로 달력 하루만 받는다.
CALENDAR_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_week(logical_date: datetime, param: str | None) -> date:
    """이 실행이 다룰 대상 주 `W`의 월요일(KST).

    `logical_date`는 UTC aware이고 스케줄이 UTC 일 22:00이라, **KST로 바꾸지 않고 요일을
    보면 한 주가 밀린다.**
    """
    if param:
        if not CALENDAR_DATE_PATTERN.match(param):
            raise ValueError(f"week_start must be YYYY-MM-DD, got {param!r}")
        week_start = date.fromisoformat(param)
        if week_start.weekday() != 0:
            raise ValueError(f"week_start must be a Monday, got {param!r}")
        return week_start
    kst_day = logical_date.astimezone(KST_TIMEZONE).date()
    run_monday = kst_day - timedelta(days=kst_day.weekday())
    return run_monday - timedelta(weeks=RUN_LAG_WEEKS)


class CausalWindow(BaseModel):
    """한 실행이 보는 구간. 값이 재시도 사이에 바뀌면 원본과 저장값이 어긋난다."""

    model_config = ConfigDict(frozen=True)

    week_start: date
    """대상 주 `W`의 월요일(KST). 자연키의 축이다."""
    week_end: date
    """`W`의 금요일. 사건이 일어난 창의 끝이다."""
    reaction_end: date
    """`W+1`의 금요일. 반응(T+5)이 확정되는 날이다."""
    as_of_at: datetime
    """event-time cutoff(UTC). 이 시각 이후 감지·평가·갱신된 행은 보지 않는다."""


def window_for(week_start: date) -> CausalWindow:
    """대상 주에서 조회 창을 만든다.

    `week_end`·`reaction_end`는 달력 금요일이다. 휴장 판정을 하지 않는 이유는
    `CUTOFF_TIME_KST` 주석에 있다 — 이 모듈이 DB를 보지 않는 근거이기도 하다.
    """
    week_end = week_start + timedelta(days=4)
    reaction_end = week_end + timedelta(days=7)
    as_of_kst = datetime.combine(reaction_end, CUTOFF_TIME_KST, tzinfo=KST_TIMEZONE)
    return CausalWindow(
        week_start=week_start,
        week_end=week_end,
        reaction_end=reaction_end,
        as_of_at=as_of_kst.astimezone(UTC),
    )


def input_hash(
    *,
    week_start: date,
    target_codes: Iterable[str],
    candidate_refs: Iterable[str],
) -> str:
    """이 실행의 입력을 한 값으로 접는다. 판정이 아니라 감사 값이다(설계 §5.4).

    **정렬한다.** 후보 조립 SQL의 반환 순서가 바뀌어도 같은 입력이면 같은 해시여야 한다.
    실행 시각과 `dag_run_id`는 넣지 않는다 — 넣으면 매번 달라져 뜻이 사라진다. 툴 호출도
    넣지 않는다. 재현의 앵커는 후보이고 툴은 `thesis_tool_call` 원장이 추적한다.
    """
    material = "|".join(
        (
            week_start.isoformat(),
            ",".join(sorted(target_codes)),
            ",".join(sorted(candidate_refs)),
            PROMPT_VERSION,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class CausalTargetKind(StrEnum):
    """대상이 어느 마스터에서 오는지. **값의 성격이 아니라 저장소를 가른다.**

    값은 `apps/models/analysis/causal.py`의 같은 이름 enum과 같아야 한다. Airflow는 `apps/`를
    보지 못해 import하지 못하므로 한 벌 더 둔다(중복 허용 + 테스트 대조 규칙).
    """

    INSTRUMENT = "instrument"
    INDEX = "index"
    QUOTE = "quote"
    INDICATOR = "indicator"


class CausalSign(StrEnum):
    """이 경로가 대상을 어느 쪽으로 밀었다고 모델이 주장하는가."""

    UP = "up"
    DOWN = "down"


class CausalConfidence(StrEnum):
    """주장의 성격. 둘 다 인과의 증명이 아니다."""

    OBSERVED = "observed"
    PLAUSIBLE = "plausible"


class CausalReturnUnit(StrEnum):
    """실현 등락의 단위. 가격은 percent, 금리는 basis_point다.

    `KTB10Y` 4.239 → 4.313은 +1.75%가 아니라 +7.4bp다. 한 칸에 섞으면 KOSPI의 +10.77%와
    크기를 비교할 수 없게 된다.
    """

    PERCENT = "percent"
    BASIS_POINT = "basis_point"


class CausalTarget(BaseModel):
    """경로가 닿는 대상 하나."""

    model_config = ConfigDict(frozen=True)

    kind: CausalTargetKind
    code: str
    provider: str | None = None
    """`indicator` 종류만 채운다. `indicator_observation`은 `(provider, series_id)`가 키라
    `series_id` 하나로 거는 쿼리는 제공처가 늘어나면 조용히 틀린다(저장소 규칙). 나머지
    종류는 제공처를 자기 마스터가 알아서 여기 안 담는다."""


# 국내 지수. 종목과 달리 마스터를 훑지 않는다 — 늘어나는 목록이 아니다.
INDEX_TARGETS: tuple[CausalTarget, ...] = (
    CausalTarget(kind=CausalTargetKind.INDEX, code="KOSPI"),
    CausalTarget(kind=CausalTargetKind.INDEX, code="KOSDAQ"),
)

# 매크로 다섯. **이것들이 대상에 있어야 그래프가 깊어진다**(설계 §3.1.1) — 대상이 못 되면
# `미국 10년물 국채금리 상승` 같은 값이 사건으로만 들어와 사슬이 거기서 끊긴다.
MACRO_TARGETS: tuple[CausalTarget, ...] = (
    CausalTarget(kind=CausalTargetKind.QUOTE, code="USDKRW"),
    CausalTarget(kind=CausalTargetKind.QUOTE, code="US10Y"),
    CausalTarget(kind=CausalTargetKind.QUOTE, code="SOX"),
    CausalTarget(kind=CausalTargetKind.QUOTE, code="VIX"),
    CausalTarget(kind=CausalTargetKind.QUOTE, code="NASDAQ100_FUT"),
)

# 금리 둘. **정책금리는 사건이자 대상이다** — 한은 인상이 사건이면 KTB10Y가 대상이고,
# 국채 급등이 사건이면 KOSPI가 대상이다. 그 겹침이 그래프를 깊게 만든다(설계 §9.3 ①).
#
# 값이 있는 구간이 아직 짧다(정책금리 2026-07-14~, 국채 2026-08-10~). 값이 없는 주는 그
# 대상만 저장되지 않고 나머지는 그대로 돈다(설계 §6).
INDICATOR_TARGETS: tuple[CausalTarget, ...] = (
    CausalTarget(kind=CausalTargetKind.INDICATOR, code="KRBASE", provider="ecos"),
    CausalTarget(kind=CausalTargetKind.INDICATOR, code="KTB10Y", provider="ecos"),
)


class TargetReturns(BaseModel):
    """대상 하나의 실현 등락. **SQL이 계산한 값이고 모델이 만들지 않는다.**

    셋이 모두 있어야 저장된다(설계 §6). 하나라도 없으면 그 대상이 통째로 빠진다 —
    NULL로 저장하면 "안 쟀다"와 "잴 수 없었다"가 나중에 구분되지 않는다.
    """

    model_config = ConfigDict(frozen=True)

    week: float
    """대상 주 안의 변화."""
    t1: float
    """주 종료 다음 KRX 거래일까지의 변화."""
    t5: float
    """주 종료 +5 KRX 거래일까지의 변화."""
    unit: CausalReturnUnit
    """가격·지수·환율은 percent, 금리는 basis_point다."""
