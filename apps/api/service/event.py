"""사건·신호·투자의견의 매핑."""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from apps.api.repository import DEFAULT_LIMIT, EventReadRepository
from apps.api.schemas import (
    AnalystOpinionList,
    AnalystOpinionRow,
    EventClaimList,
    EventClaimRow,
    EventExtractionList,
    EventExtractionRow,
    EventOutcomeList,
    EventOutcomeRow,
    SignalList,
    SignalRow,
)
from apps.api.service.common import number


def _text(value: Any) -> str:
    """Enum이면 값을, 아니면 문자열을. 행 모델이 `StrEnum`을 섞어 쓴다."""
    return str(getattr(value, "value", value))


def claim_of(row: Any) -> EventClaimRow:
    return EventClaimRow(
        id=row.id,
        stock_code=row.stock_code,
        event_type=_text(row.event_type),
        period_key=row.period_key,
        metric=_text(row.metric),
        claim_kind=_text(row.claim_kind),
        value=number(row.value),
        value_low=number(row.value_low),
        value_high=number(row.value_high),
        stated_at=row.stated_at,
        broker=row.broker,
        document_id=row.document_id,
    )


def outcome_of(row: Any) -> EventOutcomeRow:
    return EventOutcomeRow(
        stock_code=row.stock_code,
        event_type=_text(row.event_type),
        period_key=row.period_key,
        metric=_text(row.metric),
        expected_value=number(row.expected_value),
        expectation_count=row.expectation_count,
        actual_value=number(row.actual_value),
        surprise_pct=number(row.surprise_pct),
        verdict=None if row.verdict is None else _text(row.verdict),
        announced_at=row.announced_at,
        actual_ref=row.actual_ref,
    )


def extraction_of(row: Any) -> EventExtractionRow:
    return EventExtractionRow(
        document_id=row.document_id,
        extracted_at=row.extracted_at,
        claim_count=row.claim_count,
        llm_model=row.llm_model,
        prompt_version=row.prompt_version,
        extracted_content_hash=row.extracted_content_hash,
    )


def signal_of(row: Any) -> SignalRow:
    return SignalRow(
        symbol=row.symbol,
        signal_date=row.signal_date,
        kind=_text(row.kind),
        direction=_text(row.direction),
        close=number(row.close),
        sma20=number(row.sma20),
        sma60=number(row.sma60),
        rsi14=number(row.rsi14),
        macd=number(row.macd),
        macd_signal=number(row.macd_signal),
        volume_ratio20=number(row.volume_ratio20),
        rule_version=row.rule_version,
    )


def opinion_of(row: Any) -> AnalystOpinionRow:
    return AnalystOpinionRow(
        stock_code=row.stock_code,
        business_date=row.business_date,
        broker_name=row.broker_name,
        opinion=row.opinion,
        previous_opinion=row.previous_opinion,
        target_price=number(row.target_price),
        previous_close=number(row.previous_close),
        gap_amount=number(row.gap_amount),
        gap_rate=number(row.gap_rate),
    )


class EventReadService:
    """사건·신호·투자의견을 읽어 응답 계약으로 준다."""

    def __init__(self, repository: EventReadRepository) -> None:
        self._repository = repository

    async def claims(
        self,
        *,
        start: datetime,
        end: datetime,
        stock_codes: Sequence[str] = (),
        claim_kinds: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> EventClaimList:
        rows, has_more = await self._repository.claims(
            start=start,
            end=end,
            stock_codes=stock_codes,
            claim_kinds=claim_kinds,
            limit=limit,
            offset=offset,
        )
        return EventClaimList(
            items=tuple(claim_of(row) for row in rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    async def outcomes(
        self,
        *,
        start: datetime,
        end: datetime,
        stock_codes: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> EventOutcomeList:
        rows, has_more = await self._repository.outcomes(
            start=start, end=end, stock_codes=stock_codes, limit=limit, offset=offset
        )
        return EventOutcomeList(
            items=tuple(outcome_of(row) for row in rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    async def extractions(
        self, *, start: datetime, end: datetime, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> EventExtractionList:
        rows, has_more = await self._repository.extractions(
            start=start, end=end, limit=limit, offset=offset
        )
        return EventExtractionList(
            items=tuple(extraction_of(row) for row in rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    async def signals(
        self,
        *,
        start: date,
        end: date,
        symbols: Sequence[str] = (),
        kinds: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> SignalList:
        rows, has_more = await self._repository.signals(
            start=start, end=end, symbols=symbols, kinds=kinds, limit=limit, offset=offset
        )
        return SignalList(
            items=tuple(signal_of(row) for row in rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    async def opinions(
        self,
        *,
        start: date,
        end: date,
        stock_codes: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> AnalystOpinionList:
        rows, has_more = await self._repository.opinions(
            start=start, end=end, stock_codes=stock_codes, limit=limit, offset=offset
        )
        return AnalystOpinionList(
            items=tuple(opinion_of(row) for row in rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )
