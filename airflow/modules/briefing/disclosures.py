"""새 공시 알림의 조회·렌더링.

설계는 `docs/briefing/disclosure-briefing.md`다.

**이 모듈은 LangChain을 import하지 않는다.** DAG 파일이 최상단에서 이것을 끌고 오므로
무거운 것이 섞이면 DagBag 30초 타임아웃에 걸린다. 강조를 고르는 층은
`disclosure_picks.py`에 있고 DAG이 태스크 안에서 늦게 import한다.
`tests/modules/test_import_weight.py`가 그 경계를 잰다.

## 창은 벽시계가 아니라 data interval이다

`(window_start, window_end]` 반열림이라 실행이 밀려도 창이 이어지고 한 공시가 두 창에
걸치지 않는다. `receipt_date`가 아니라 `detected_at`으로 거르는 이유는 접수일에 시·분이
없어 시각으로 자를 수 없고, 우리가 실제로 알 수 있었던 시점이 감지 시각이기 때문이다.

## 숫자는 SQL과 순수 함수가 만든다

실적 금액과 전년 대비는 `earnings_fact`에서 읽어 `year_over_year`가 계산한다. 모델은
산문만 쓴다. 숫자 비교에 LLM을 쓰지 않는 것은 `stock_event_*` 계열과 같은 규칙이다.

## 0건이면 아무 것도 하지 않는다

문서 브리핑은 0건에도 보내 생존 신호를 겸하지만, 여기서 그러면 하루의 대부분이
"공시 없음"이라 아무도 안 읽는다. 수집 생존은 `slack_ops_briefing`이 이미 보고한다.
"""

import json
import logging
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from modules.briefing import blocks
from modules.db import Connection
from modules.sql import read_sql
from modules.thesis_domain import DART_VIEWER_URL
from modules.utility import KST_TIMEZONE

logger = logging.getLogger(__name__)

NEW_DISCLOSURES = read_sql("postgres", "disclosure_event", "select_new_for_briefing.sql")
EARNINGS_BY_RCEPT = read_sql("postgres", "earnings_fact", "select_by_rcept_no.sql")

# 강조 이유 한 줄의 상한. 넘으면 그 건만 잘라 낸다. 값이 프롬프트에도 f-string으로 실린다.
MAX_REASON_CHARS = 120

# 한 메시지에 그리는 공시의 상한. 두 종목이라 정상 창에서는 한두 건이고, 이 값은 폭주만
# 받는 안전망이다 — 정정 공시가 무더기로 올라온 날 메시지가 Slack 블록 상한에 걸리는 것을 막는다.
MAX_DISCLOSURES = 20

# 지표 표시 이름. 키는 `EarningsMetric`의 저장값과 같다.
METRIC_LABELS = {"revenue": "매출", "operating_profit": "영업이익", "net_income": "순이익"}

# 재무제표 범위 표기. 연결이 기본이라 별도일 때만 화면에 밝힌다.
SCOPE_LABELS = {"CFS": "연결", "OFS": "별도"}

# 강조 실패 사유를 채널에 적는 길이. 원문은 로그에 그대로 남는다.
# Pydantic·LangChain 예외는 수백 자에 URL까지 달고 오는데, 그걸 그대로 실으면 공시보다
# 오류가 길어진다. 사람이 "강조가 왜 없나"를 알 만큼만 싣는다(2026-08-27 실측).
MAX_ERROR_CHARS = 160


class HighlightError(RuntimeError):
    """모델이 쓸 수 있는 강조 결과를 내지 않았다."""


class Highlight(BaseModel):
    """강조할 공시 하나. `rcept_no`는 후보 목록 안의 값이다."""

    model_config = ConfigDict(frozen=True)

    rcept_no: str
    reason: str = Field(default="", description=f"왜 주목해야 하는지 한 줄. {MAX_REASON_CHARS}자 이내")


class Highlights(BaseModel):
    """모델 응답. 스키마를 강제하되 강제가 안 되는 제공처를 위해 검증도 남긴다."""

    model_config = ConfigDict(frozen=True)

    highlights: tuple[Highlight, ...] = ()


class EarningsLine(BaseModel):
    """공시 하나에 붙은 지표 한 줄. 금액은 원 단위 저장값 그대로다."""

    model_config = ConfigDict(frozen=True)

    metric: str
    statement_scope: str
    current_amount: Decimal
    prior_year_amount: Decimal | None = None


class NewDisclosure(BaseModel):
    """이 창에서 처음 감지된 공시 한 건."""

    model_config = ConfigDict(frozen=True)

    rcept_no: str
    stock_code: str
    company_name: str
    report_name: str
    receipt_date: date
    detected_at: AwareDatetime
    remarks: str | None = None
    earnings: tuple[EarningsLine, ...] = ()

    @property
    def viewer_url(self) -> str:
        return DART_VIEWER_URL.format(rcept_no=self.rcept_no)


class DisclosureBatch(BaseModel):
    """한 실행이 보는 것 전부. 비어 있으면 아무 것도 보내지 않는다."""

    model_config = ConfigDict(frozen=True)

    generated_at: AwareDatetime
    window_start: AwareDatetime
    window_end: AwareDatetime
    disclosures: tuple[NewDisclosure, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.disclosures

    @property
    def allowed_ids(self) -> frozenset[str]:
        """강조 응답을 거를 때 쓰는 접수번호 집합."""
        return frozenset(disclosure.rcept_no for disclosure in self.disclosures)

    def by_rcept_no(self, rcept_no: str) -> NewDisclosure | None:
        for disclosure in self.disclosures:
            if disclosure.rcept_no == rcept_no:
                return disclosure
        return None


def collect_batch(
    connection: Connection,
    now: datetime,
    window_start: datetime,
    window_end: datetime,
    max_disclosures: int = MAX_DISCLOSURES,
) -> DisclosureBatch:
    """창 안의 새 공시와 거기 붙은 실적 숫자를 함께 읽는다."""
    with connection.cursor() as cursor:
        cursor.execute(NEW_DISCLOSURES, (window_start, window_end))
        rows = list(cursor.fetchall())

        head = rows[:max_disclosures]
        if len(rows) > len(head):
            # 조용히 자르지 않는다. 두 종목뿐이라 이 자리가 보이면 수집 쪽에 사고가 있는 것이다.
            logger.warning("dropped %s disclosures over the message cap", len(rows) - len(head))

        earnings: dict[str, list[EarningsLine]] = {}
        if head:
            cursor.execute(EARNINGS_BY_RCEPT, ([row[0] for row in head],))
            for fact in cursor.fetchall():
                earnings.setdefault(fact[0], []).append(
                    EarningsLine(
                        metric=fact[1],
                        statement_scope=fact[2],
                        current_amount=fact[4],
                        prior_year_amount=fact[5],
                    )
                )

    return DisclosureBatch(
        generated_at=now,
        window_start=window_start,
        window_end=window_end,
        disclosures=tuple(
            NewDisclosure(
                rcept_no=row[0],
                stock_code=row[1],
                company_name=row[2],
                report_name=row[3],
                receipt_date=row[4],
                detected_at=row[5],
                remarks=row[6],
                earnings=tuple(earnings.get(row[0], ())),
            )
            for row in head
        ),
    )


def year_over_year(current: Decimal, prior: Decimal | None) -> Decimal | None:
    """전년 대비 증감률(퍼센트). 분모가 없거나 0 이하면 `None`이다.

    **0으로 메우지 않는다.** 결측과 "변화 없음"이 같아지면 화면이 거짓말을 한다.
    분모가 음수인 경우(적자에서 흑자로)도 비율이 뜻을 잃으므로 `None`이다.
    """
    if prior is None or prior <= 0:
        return None
    return (current - prior) / prior * 100


def format_amount(amount: Decimal) -> str:
    """원 단위 저장값을 사람이 읽는 표기로. 조·억 단위와 천 단위 쉼표를 쓴다.

    단위를 반드시 붙인다 — Slack은 프론트엔드가 없는 출력이라 표기가 유일한 단서다.
    """
    sign = "-" if amount < 0 else ""
    value = abs(int(amount))
    trillion, remainder = divmod(value, 1_000_000_000_000)
    hundred_million = remainder // 100_000_000
    if trillion:
        tail = f" {hundred_million:,}억" if hundred_million else ""
        return f"{sign}{trillion:,}조{tail} 원"
    if hundred_million:
        return f"{sign}{hundred_million:,}억 원"
    return f"{sign}{value:,}원"


def pick_input(batch: DisclosureBatch) -> str:
    """강조에 줄 입력. 보고서명과 실적 숫자까지다.

    **시각은 KST로 준다.** UTC ISO를 그대로 실으면 모델이 "오늘"을 하루 어긋나게 읽는다.
    """
    payload = {
        "as_of_kst": batch.generated_at.astimezone(KST_TIMEZONE).isoformat(),
        "disclosures": [
            {
                "rcept_no": disclosure.rcept_no,
                "stock_code": disclosure.stock_code,
                "company_name": disclosure.company_name,
                "report_name": disclosure.report_name,
                "receipt_date": disclosure.receipt_date.isoformat(),
                "detected_at_kst": disclosure.detected_at.astimezone(KST_TIMEZONE).isoformat(),
                "remarks": disclosure.remarks,
                "earnings": [
                    {
                        "metric": line.metric,
                        "statement_scope": line.statement_scope,
                        "amount": format_amount(line.current_amount),
                        "year_over_year": _yoy_text(line),
                    }
                    for line in disclosure.earnings
                ],
            }
            for disclosure in batch.disclosures
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_blocks(
    batch: DisclosureBatch,
    highlights: Sequence[Highlight] | None = None,
    error: str | None = None,
) -> list[dict[str, Any]]:
    """공시를 전부 그린다. 강조된 것에만 별과 이유가 붙는다."""
    local = batch.generated_at.astimezone(KST_TIMEZONE)
    reasons = {highlight.rcept_no: highlight.reason for highlight in highlights or ()}
    rendered = [blocks.header(f"📄 새 공시 {len(batch.disclosures)}건 · {blocks.timestamp(local)}")]

    rendered += [
        blocks.section(_disclosure_text(disclosure, reasons.get(disclosure.rcept_no)))
        for disclosure in batch.disclosures
    ]

    if error:
        # 조용히 빠지지 않는다. 강조가 없는 알림과 실패한 알림은 구분돼야 한다.
        rendered.append(blocks.context([f"⚠️ 공시 강조 실패: {_short_error(error)}"]))
    return rendered


def _short_error(error: str) -> str:
    """첫 줄만, 그리고 상한까지. 전체는 Airflow 로그가 갖는다."""
    head = error.strip().splitlines()[0] if error.strip() else error
    return head if len(head) <= MAX_ERROR_CHARS else head[:MAX_ERROR_CHARS].rstrip() + "…"


def render_text(batch: DisclosureBatch, highlights: Sequence[Highlight] | None = None) -> str:
    """블록을 못 그리는 자리(알림, 검색 결과)에 뜨는 대체 문구."""
    return f"새 공시 {len(batch.disclosures)}건 · {_head_title(batch, highlights)}"


def _head_title(batch: DisclosureBatch, highlights: Sequence[Highlight] | None) -> str:
    for highlight in highlights or ():
        disclosure = batch.by_rcept_no(highlight.rcept_no)
        if disclosure:
            return f"{disclosure.company_name} {disclosure.report_name}"
    if not batch.disclosures:
        return "새 공시 없음"
    first = batch.disclosures[0]
    return f"{first.company_name} {first.report_name}"


def _disclosure_text(disclosure: NewDisclosure, reason: str | None) -> str:
    mark = "⭐ " if reason else ""
    lines = [
        f"{mark}*{disclosure.company_name}* `{disclosure.stock_code}`",
        f"<{disclosure.viewer_url}|{disclosure.report_name}>",
    ]
    if disclosure.earnings:
        lines.append(" · ".join(_earnings_text(line) for line in disclosure.earnings))
    if reason:
        lines.append(reason)
    if disclosure.remarks:
        lines.append(f"비고: {disclosure.remarks}")
    local = disclosure.detected_at.astimezone(KST_TIMEZONE)
    lines.append(f"_최초 감지 {local:%m/%d %H:%M} KST · 접수 {disclosure.receipt_date}_")
    return "\n".join(lines)


def _earnings_text(line: EarningsLine) -> str:
    scope = "" if line.statement_scope == "CFS" else f"({SCOPE_LABELS.get(line.statement_scope, line.statement_scope)})"
    change = _yoy_text(line)
    tail = f" (전년 대비 {change})" if change else ""
    return f"{METRIC_LABELS.get(line.metric, line.metric)}{scope} {format_amount(line.current_amount)}{tail}"


def _yoy_text(line: EarningsLine) -> str | None:
    change = year_over_year(line.current_amount, line.prior_year_amount)
    return None if change is None else f"{change:+.1f}%"
