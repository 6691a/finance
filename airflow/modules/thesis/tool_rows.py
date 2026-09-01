"""조회 행 하나를 모델이 읽을 응답 모델로 바꾼다. **순수 함수뿐이다.**

`tools.py`가 그 응답의 *모양*을 갖고 여기는 *변환*을 갖는다. DB도 LangChain도 모르므로
행 튜플만 주면 테스트가 된다 — 컬럼 순서와 단위(bp 대 퍼센트) 실수가 여기서 잡힌다.

이름 앞의 밑줄을 떼면서 나왔다. `toolbox.py` 안에 있을 때는 사적 헬퍼였지만 이제 모듈
경계를 넘으므로 공개 이름이다.
"""

import logging
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from modules.technical import indicators
from modules.thesis.domain import (
    BASIS_POINT_KINDS,
    DOMESTIC_MAX_DAILY_CHANGE_PCT,
    MAX_ITEM_DETAIL_CHARS,
    MAX_OPINION_REASON_CHARS,
    Evidence,
    kst_label,
)
from modules.thesis.tools import (
    DocumentDetail,
    IndicatorDetail,
    MacroDetail,
    OpinionDetail,
    PendingExpectationDetail,
    SurpriseDetail,
    UsCloseDetail,
)

logger = logging.getLogger(__name__)


def tool_row(item: Evidence) -> dict[str, Any]:
    """모델에게 보이는 모양. `ref`가 인용 키라 항상 첫 칸이다.

    **여기만 dict를 만든다.** `Evidence`의 머리 세 칸과 상세를 한 단으로 펴는 자리라
    모델 하나로 표현할 수 없고, 상세 종류마다 모델을 두면 머리 세 칸을 다섯 번 베끼게 된다.
    Slack 블록·JSON Schema와 같은 wire 조립 경계다.
    """
    row: dict[str, Any] = {"ref": item.ref, "title": item.title, **item.detail.model_dump(mode="json")}
    if item.url:
        row["url"] = item.url
    return row


def document_detail(row: Sequence[Any]) -> DocumentDetail:
    """문서 한 건이 모델에게 보여 줄 값.

    `new_facts`와 `reason`을 함께 준다. 제목·점수만 주면 이유 문장을 쓸 재료가 없어 모델이
    근거를 지어낸다. 둘 합계가 길면 자른다 — 한 건이 컨텍스트를 다 먹으면 안 된다.
    """
    new_facts = list(row[7] or ())
    reason = row[8] or ""
    budget = MAX_ITEM_DETAIL_CHARS - len(reason)
    kept: list[str] = []
    for fact in new_facts:
        if budget - len(fact) < 0:
            break
        kept.append(fact)
        budget -= len(fact)
    return DocumentDetail(
        source=row[3],
        published_at=row[4].isoformat() if row[4] else None,
        value_score=row[5],
        direction=row[6],
        new_facts=tuple(kept),
        reason=reason[:MAX_ITEM_DETAIL_CHARS],
        tickers=tuple(row[9] or ()),
    )


def opinion_detail(row: Sequence[Any]) -> OpinionDetail:
    """투자의견 한 건. 같은 증권사·같은 날 리포트 요약이 있으면 사유로 함께 준다.

    KIS는 숫자만 주고 왜 그 의견인지는 안 준다. 사유가 없으면 모델이 목표가 숫자만 보고
    이유를 지어낸다. 요약은 길어서 자른다 — 스무 건이 컨텍스트를 다 먹으면 안 된다.
    """
    reason = row[7] if len(row) > 7 else None
    return OpinionDetail(
        business_date=row[0],
        broker_name=row[1],
        opinion=row[2],
        previous_opinion=row[3],
        target_price=as_float(row[4]),
        previous_close=as_float(row[5]),
        gap_rate=as_float(row[6]),
        reason=str(reason)[:MAX_OPINION_REASON_CHARS] if reason else None,
    )


def surprise_detail(row: Sequence[Any]) -> SurpriseDetail:
    """기대 대비 발표 판정 한 건. 금액은 원 단위 그대로 준다 — 모델이 자릿수를 바꾸지 않게."""
    return SurpriseDetail(
        event_type=row[0],
        period_key=row[1],
        metric=row[2],
        expected_value=as_float(row[3]),
        expectation_count=row[4],
        actual_value=as_float(row[5]),
        surprise_pct=as_float(row[6]),
        verdict=row[7],
        announced_at=row[8],
    )


def pending_expectation_detail(row: Sequence[Any]) -> PendingExpectationDetail:
    """아직 발표되지 않은 이벤트의 대표 기대치. 발표가 나오면 이 숫자가 기준선이다."""
    return PendingExpectationDetail(
        event_type=row[0],
        period_key=row[1],
        metric=row[2],
        expected_value=as_float(row[3]),
        expectation_count=row[4],
        latest_stated_at=row[5],
    )


def macro_detail(row: Sequence[Any]) -> MacroDetail:
    """심볼 하나의 변화. **축이 둘이다** — 분석 창과 전일 종가.

    전일 종가 대비를 함께 주는 이유는 추론과 채점의 기준가가 그것이기 때문이다. 창 변화만
    주면 창 밖으로 빠진 개장 갭이 사라진 값을 모델이 하루 등락으로 읽는다.

    **금리는 퍼센트가 아니라 bp로 준다.** 4.65→4.70을 `+1.08%`로 주면 모델이 급등으로 읽는다
    (`briefing/market.py`의 `QUOTED_KINDS`와 같은 이유). 두 축에 같은 규칙을 쓴다.

    `previous_close`는 봉에 실려 오는 값이라 심볼에 따라 없을 수 있다. 없으면 그 축의 칸
    셋이 통째로 빠진다 — 창 종가로 대신 채우면 전일 대비가 늘 0으로 보인다.
    """
    kind, first_close, last_close, previous_close = row[3], row[5], row[6], row[10]
    return MacroDetail(
        kind=kind,
        country=row[4],
        first_close=float(first_close),
        last_close=float(last_close),
        window_start=row[7].isoformat(),
        window_end=row[8].isoformat(),
        bar_count=row[9],
        change_bp=change_bp(kind, first_close, last_close),
        change_pct=change_pct(kind, first_close, last_close),
        previous_close=float(previous_close) if previous_close is not None else None,
        prev_close_change_bp=change_bp(kind, previous_close, last_close),
        prev_close_change_pct=change_pct(kind, previous_close, last_close),
    )


def change_bp(kind: str, base: Decimal | None, value: Decimal) -> float | None:
    """금리 계열만 bp를 갖는다. 기준값이 없으면 잰 것이 없다."""
    if kind not in BASIS_POINT_KINDS or base is None:
        return None
    return round(float(value - base) * 100, 1)


def change_pct(kind: str, base: Decimal | None, value: Decimal) -> float | None:
    """금리가 아닌 계열의 퍼센트 변화. 기준값이 없거나 0이면 잰 것이 없다."""
    if kind in BASIS_POINT_KINDS or not base:
        return None
    return round(float((value - base) / base) * 100, 2)


def us_close_detail(row: Sequence[Any]) -> UsCloseDetail:
    """심볼 하나의 마감 값. 비교 대상은 창의 첫 봉이 아니라 **전일 정규장 종가**다.

    시각은 `closed_at_kst` 한 칸이고 이름이 시간대를 밝힌다. 다른 툴의 시각 칸은 UTC라
    프롬프트가 "9시간을 더한다"고 알리는데, 마감 시각은 모델이 "어느 날 장이었나"를
    정하는 데 쓰므로 표시 시간대로 준다(`kst_label`).
    """
    kind, close, previous_close = row[3], row[4], row[5]
    return UsCloseDetail(
        kind=kind,
        close=float(close),
        previous_close=float(previous_close),
        closed_at_kst=kst_label(row[6]),
        change_bp=change_bp(kind, previous_close, close),
        change_pct=change_pct(kind, previous_close, close),
    )


def macro_title(row: Sequence[Any]) -> str:
    """`macro_changes` 한 줄의 제목. **축을 글자로 밝힌다.**

    Slack 근거 줄이 이 문자열만 그리므로 여기서 안 밝히면 읽는 쪽이 창 변화를 하루 등락으로
    읽는다(`thesis/render.py`의 `_baseline_line`과 같은 이유). 전일 종가가 봉에 없으면 창
    축만 적는다 — 없는 값을 "0.00%"로 지어내지 않는다.
    """
    kind, label, first_close, last_close, previous_close = row[3], row[2], row[5], row[6], row[10]
    title = f"{label} 창 {change_label(kind, first_close, last_close)}"
    if previous_close:
        title += f" · 전일 종가 대비 {change_label(kind, previous_close, last_close)}"
    return title


def change_label(kind: str, base: Decimal, value: Decimal) -> str:
    """제목 뒤에 붙는 변화 표기. Slack 근거 줄에도 그대로 쓰인다."""
    if kind in BASIS_POINT_KINDS:
        return f"{float(value - base) * 100:+.1f}bp"
    if not base:
        return "변화 없음"
    return f"{float((value - base) / base) * 100:+.2f}%"


def technical_snapshot(subject_code: str, rows: Sequence[Sequence[Any]]) -> indicators.TechnicalSnapshot | None:
    """`technical/select_history.sql` 행에서 지표 한 벌을 만든다. 못 만들면 `None`이다.

    조회는 최신순이고 계산기는 오름차순을 받는다. **국내 KIS 행에만 35% 단절 guard를 건다** —
    해외 지수·환율은 가격제한폭이 없어 같은 잣대를 댈 수 없다.
    """
    ascending = list(reversed(rows))
    try:
        bars = [
            indicators.DailyBar(
                business_date=row[5],
                open=float(row[6]),
                high=float(row[7]),
                low=float(row[8]),
                close=float(row[9]),
                volume=None if row[10] is None else int(row[10]),
            )
            for row in ascending
        ]
    except (TypeError, ValueError, ValidationError) as error:
        # 원천 값이 계약을 깨면 지표를 만들지 않는다. 원시 봉은 그대로 나간다.
        # **로그는 남긴다** — 조용히 빠지면 프롬프트에 지표가 없는 이유를 아무도 모른다.
        logger.warning("technical snapshot for %s skipped: %s", subject_code, error)
        return None
    domestic_kis = ascending[0][0] == "kis" and ascending[0][4] == "KR"
    return indicators.summarize(
        subject_code,
        str(ascending[0][2] or subject_code),
        bars,
        max_abs_daily_change_pct=DOMESTIC_MAX_DAILY_CHANGE_PCT if domestic_kis else None,
    )


def as_float(value: Decimal | float | None) -> float | None:
    """`Decimal`을 JSON이 읽는 수로 바꾼다. `None`은 그대로 둔다.

    **0으로 채우지 않는다.** 결측(아직 안 들어온 값)과 실제 0은 다른 뜻이고, 모델이
    "순매수 0"을 관측으로 읽으면 없는 사실을 근거로 쓴다.

    숫자가 아닌 것이 오면 `TypeError`로 죽는다. 예전에는 `date`도 그대로 통과시켜서
    "숫자 칸"이라고 적힌 자리에 날짜가 실릴 수 있었다.
    """
    return None if value is None else float(value)


def indicator_row(row: Sequence[Any], *, as_basis_points: bool) -> IndicatorDetail:
    """지표 계열 하나의 최신값과 직전값 대비 변화.

    **금리는 bp로 준다.** 4.65에서 4.70으로 가는 것은 `+1.08%`가 아니라 `+5bp`다
    (`change_label`과 같은 이유). 물가지수처럼 퍼센트가 아닌 계열은 변화를 값 그대로 준다.

    직전값이 없으면 `change`를 만들지 않는다. 첫 관측을 0 변화로 꾸미지 않기 위해서다.
    """
    value, previous = row[9], row[10]
    change: float | None = None
    if value is not None and previous is not None:
        difference = Decimal(value) - Decimal(previous)
        change = round(float(difference) * 100, 1) if as_basis_points else round(float(difference), 4)
    return IndicatorDetail(
        provider=row[0],
        series_id=row[1],
        country=row[2],
        country_name=row[3],
        label=row[4],
        maturity_months=row[6],
        unit=row[7],
        observation_date=row[8],
        value=as_float(value),
        previous_date=row[11],
        previous_value=as_float(previous),
        change_bp=change if as_basis_points else None,
        change=None if as_basis_points else change,
    )


def clamp_int(value: Any, low: int, high: int, fallback: int) -> int:
    """모델이 넘긴 인자를 허용 범위로 자른다.

    범위를 벗어난 값에 오류로 답하지 않고 잘라서 실행한다. 상한은 우리가 지키면 되는 것이고,
    한 번 더 왕복하는 값어치가 없다. 숫자가 아니면 기본값을 쓴다.
    """
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(low, min(high, number))
