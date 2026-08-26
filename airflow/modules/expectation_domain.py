"""기대·실제 판정의 어휘와 셈 — 상수, 저장 모양, 순수 함수.

**이 모듈은 LangChain을 import하지 않는다.** 판정은 LLM이 없고(모듈 나누기 전 docstring),
추출만 있다. 그 경계를 파일로 만든 것이 이 분리다 — `expectation_judgment`는 여기만 보고
`expectation_extraction`이 무거운 쪽을 갖는다.

기간 표기·단위 정규화·대표 기대치 집계·beat/meet/miss 분류가 전부 여기 순수 함수다.
thesis 채점 수식이 SQL이 아니라 Python에 있는 것과 같은 이유로, DB 없이 경계값을 테스트한다.

이벤트·지표 상수는 `apps/models/analysis/events.py`와 **중복을 허용하되 테스트로 대조한다**
(Airflow 트리가 `apps/`를 못 보기 때문이다).
"""

import logging
import re
import statistics
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

logger = logging.getLogger(__name__)

# 프롬프트를 고치면 올린다. 이 값이 오른 문서는 재추출 대상이 된다.
PROMPT_VERSION = "1"

# 한 번 실행에서 추출할 문서 수. 대상이 종목 태그 문서뿐이라 보통 이보다 훨씬 적다.
DEFAULT_BATCH_SIZE = 50

# meet로 볼 서프라이즈 허용 밴드(퍼센트). **실측이 아니라 시작값이다** — thesis의
# FLAT_THRESHOLD_PCT처럼 판정 분포가 쌓이면 다시 정한다(docs/analysis/market-thesis/TUNING.md).
MEET_BAND_PCT = Decimal("5.0")

# 아래 도메인 상수는 apps/models/analysis/events.py와 중복이다(모듈 docstring). 테스트가 대조한다.
EVENT_TYPES: tuple[str, ...] = ("shareholder_return", "earnings", "guidance")
CLAIM_KINDS: tuple[str, ...] = ("expectation", "actual")
EVENT_METRICS: dict[str, tuple[str, ...]] = {
    "shareholder_return": ("total_return_amount", "buyback_amount", "dividend_total", "dividend_per_share"),
    "earnings": ("revenue", "operating_profit", "net_income"),
    "guidance": ("revenue", "operating_profit"),
}
PERIOD_KEY_PATTERN = re.compile(r"^[0-9]{4}(Q[1-4]|H[12])?$")

# 원문 단위 표기를 원(KRW)으로 바꾸는 배수. 모르는 표기는 그 주장을 버린다 — 조용히
# 엉뚱한 자릿수로 저장하는 것보다 낫다(재무성 和暦 규칙과 같은 태도).
UNIT_MULTIPLIERS: dict[str, Decimal] = {
    "원": Decimal(1),
    "만원": Decimal(10) ** 4,
    "억": Decimal(10) ** 8,
    "억원": Decimal(10) ** 8,
    "조": Decimal(10) ** 12,
    "조원": Decimal(10) ** 12,
}

# 지표 한글 표기. Slack과 로그가 쓴다.
METRIC_LABELS: dict[str, str] = {
    "total_return_amount": "총 환원액",
    "buyback_amount": "자사주 매입",
    "dividend_total": "배당 총액",
    "dividend_per_share": "주당 배당금",
    "revenue": "매출액",
    "operating_profit": "영업이익",
    "net_income": "당기순이익",
}
EVENT_LABELS: dict[str, str] = {
    "shareholder_return": "주주환원",
    "earnings": "실적",
    "guidance": "가이던스",
}
VERDICT_LABELS: dict[str, str] = {
    "beat": "▲ 상회",
    "meet": "– 부합",
    "miss": "▼ 미달",
}
class ExtractionError(RuntimeError):
    """모델이 우리가 아는 모양으로 답하지 않았다. 문서는 원장에 오르지 않고 다음 실행이 다시 집는다."""


# ---------------------------------------------------------------------------
# 순수 함수 — 기간·단위·집계·분류. LLM도 DB도 모른다.
# ---------------------------------------------------------------------------


def period_end_for(period_key: str) -> date:
    """기간 표기를 회계 기간 종료일로 바꾼다. `earnings_fact.period_end`와 조인하는 키다."""
    if not PERIOD_KEY_PATTERN.match(period_key):
        raise ValueError(f"period_key must match YYYY, YYYYQn or YYYYHn: {period_key!r}")
    year = int(period_key[:4])
    suffix = period_key[4:]
    if not suffix:
        return date(year, 12, 31)
    quarter_ends = {"Q1": (3, 31), "Q2": (6, 30), "Q3": (9, 30), "Q4": (12, 31), "H1": (6, 30), "H2": (12, 31)}
    month, day = quarter_ends[suffix]
    return date(year, month, day)


def amount_basis_for(period_key: str) -> str:
    """기간 표기가 요구하는 `earnings_fact.amount_basis`.

    분기·반기는 해당 기간 금액(period), 연간은 사업연도 누계(cumulative)다.
    """
    if not PERIOD_KEY_PATTERN.match(period_key):
        raise ValueError(f"period_key must match YYYY, YYYYQn or YYYYHn: {period_key!r}")
    return "cumulative" if len(period_key) == 4 else "period"


def normalize_amount(value: str, unit: str) -> Decimal | None:
    """원문 표기 값을 원(KRW)으로 정규화한다. 모르는 표기는 None — 부르는 쪽이 그 주장을 버린다."""
    multiplier = UNIT_MULTIPLIERS.get(unit.replace(" ", ""))
    if multiplier is None:
        return None
    try:
        amount = Decimal(value.replace(",", "").strip())
    except InvalidOperation:
        return None
    return (amount * multiplier).quantize(Decimal("0.01"))


class ClaimRow(BaseModel):
    """판정 조회가 돌려준 주장 한 행."""

    model_config = ConfigDict(frozen=True)

    stock_code: str
    event_type: str
    period_key: str
    metric: str
    claim_kind: str
    value: Decimal
    stated_at: datetime
    broker: str | None
    document_id: int | None
    source_record_id: int | None


def resolve_actual(rows: Sequence[ClaimRow]) -> tuple[Decimal, datetime, str] | None:
    """actual 주장들에서 실제값 하나를 정한다. 값이 갈리면 판정하지 않는다.

    "총 환원 8조"와 "배당+자사주 8.5조"처럼 집계 범위가 다른 숫자가 실제로 온다. 조용히
    한쪽을 고르는 대신 보류한다 — 공시 기반 기사가 수렴하면 다음 실행이 푼다. 반환은
    (실제값, 발표 시각 = 가장 이른 주장 시각, actual_ref)다.
    """
    actuals = [row for row in rows if row.claim_kind == "actual"]
    if not actuals:
        return None
    values = {row.value for row in actuals}
    if len(values) > 1:
        first = actuals[0]
        logger.warning(
            "conflicting actual values for %s %s %s %s: %s — holding judgment",
            first.stock_code,
            first.event_type,
            first.period_key,
            first.metric,
            sorted(values),
        )
        return None
    earliest = min(actuals, key=lambda row: row.stated_at)
    return earliest.value, earliest.stated_at, f"document:{earliest.document_id}"


def aggregate_expectations(rows: Sequence[ClaimRow], announced_at: datetime) -> tuple[Decimal, int] | None:
    """발표 전 기대들에서 대표 기대치를 뽑는다. 기대가 없으면 None — 판정하지 않는다.

    - `stated_at < announced_at`인 기대만 쓴다. 발표 뒤 "기대치는 X였다"라고 회고한 기사가
      기대로 섞이면 판정이 오염된다.
    - 컨센서스 행(`source_record_id` 출처)이 있으면 **최신 컨센서스 하나**다. 정형 집계가
      개별 리포트 발췌보다 정확하다.
    - 없으면 주체(broker)별 최신 행의 **중앙값**이다. 같은 증권사가 기대를 올려 잡았으면
      최신만 센다. 주체를 모르는 기사 인용(broker NULL)도 한 표다.

    반환은 (대표 기대치, 대조한 기대 행 수)다.
    """
    expectations = [row for row in rows if row.claim_kind == "expectation" and row.stated_at < announced_at]
    if not expectations:
        return None
    consensus = [row for row in expectations if row.source_record_id is not None]
    if consensus:
        latest = max(consensus, key=lambda row: row.stated_at)
        return latest.value, len(expectations)
    latest_by_broker: dict[str | None, ClaimRow] = {}
    for row in expectations:
        current = latest_by_broker.get(row.broker)
        if current is None or row.stated_at > current.stated_at:
            latest_by_broker[row.broker] = row
    values = [row.value for row in latest_by_broker.values()]
    return Decimal(statistics.median(values)), len(expectations)


def classify_surprise(
    expected: Decimal,
    actual: Decimal,
    band_pct: Decimal = MEET_BAND_PCT,
) -> tuple[Decimal, str] | None:
    """서프라이즈를 분류한다. |surprise| ≤ 밴드면 meet, 아니면 부호로 beat/miss.

    기대가 0이면 비율을 만들 수 없다. 실제도 0이면 정확히 부합(meet, 0%)이고, 아니면
    판정하지 않는다(None) — 억지 비율이 더 나쁘다.
    """
    if expected == 0:
        if actual == 0:
            return Decimal("0.0000"), "meet"
        return None
    surprise = ((actual - expected) / abs(expected) * 100).quantize(Decimal("0.0001"))
    if abs(surprise) <= band_pct:
        return surprise, "meet"
    return surprise, "beat" if surprise > 0 else "miss"


class EarningsFactRow(BaseModel):
    """`earnings_fact/select_actual_for_judgment.sql`이 돌려준 실적 행."""

    model_config = ConfigDict(frozen=True)

    id: int
    statement_scope: str
    amount_basis: str
    release_type: str
    rcept_no: str
    current_amount: Decimal
    created_at: datetime


def resolve_earnings_actual(rows: Sequence[EarningsFactRow], period_key: str) -> tuple[Decimal, datetime, str] | None:
    """실적 실제값을 earnings_fact 후보에서 고른다.

    기간 기준(`amount_basis_for`)이 맞는 행 중 연결(CFS)을 별도(OFS)보다 우선하고, 같은
    범위 안에서는 최신 접수번호(정정 공시)를 쓴다 — `EarningsFact` docstring의 조회 규칙
    그대로다. `created_at`은 우리가 그 공시를 파싱한 시각이라 발표 감지 시각의 대용이다.
    """
    basis = amount_basis_for(period_key)
    candidates = [row for row in rows if row.amount_basis == basis]
    if not candidates:
        return None
    for scope in ("CFS", "OFS"):
        scoped = [row for row in candidates if row.statement_scope == scope]
        if scoped:
            # 조회 SQL이 rcept_no 내림차순으로 준다 — 첫 행이 최신 정정이다.
            chosen = scoped[0]
            return chosen.current_amount, chosen.created_at, f"earnings_fact:{chosen.id}"
    return None


def format_krw(amount: Decimal) -> str:
    """원 단위 금액을 사람이 읽는 표기로. 조·억 단위로 줄이고 작은 값은 원 그대로 둔다."""
    magnitude = abs(amount)
    if magnitude >= Decimal(10) ** 12:
        return f"{amount / Decimal(10) ** 12:,.2f}조"
    if magnitude >= Decimal(10) ** 8:
        return f"{amount / Decimal(10) ** 8:,.0f}억"
    return f"{amount:,.0f}원"


# ---------------------------------------------------------------------------
# 추출 — LLM은 여기에만 있다.
# ---------------------------------------------------------------------------


class PendingExtractionDocument(BaseModel):
    """추출을 기다리는 문서. `tickers`는 평가가 붙인 종목 태그다."""

    model_config = ConfigDict(frozen=True)

    id: int
    source_slug: str
    title: str
    summary: str | None
    body: str | None
    published_at: datetime | None
    detected_at: datetime
    content_hash: str
    tickers: tuple[str, ...]

    @property
    def stated_at(self) -> datetime:
        """주장 시점. 발행 시각이 없으면 감지 시각이다. 모델이 아니라 코드가 정한다."""
        return self.published_at or self.detected_at


class NormalizedClaim(BaseModel):
    """검증·정규화를 통과해 저장할 주장 하나. 값은 원(KRW)이다."""

    model_config = ConfigDict(frozen=True)

    stock_code: str
    event_type: str
    period_key: str
    metric: str
    claim_kind: str
    value: Decimal
    value_low: Decimal | None
    value_high: Decimal | None
    broker: str | None
