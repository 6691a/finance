"""수집 원장과 마스터의 매핑.

**요약을 늦은 순으로 정렬한다.** 이 화면을 여는 이유가 "무엇이 안 들어오고 있나"라서,
가장 오래 소식 없는 출처가 맨 위여야 한다.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from apps.api.repository import DEFAULT_LIMIT, CollectionReadRepository
from apps.api.schemas import (
    InstrumentList,
    InstrumentRow,
    MarketSessionList,
    MarketSessionRow,
    SourceHealth,
    SourceHealthList,
    SourceRecordList,
    SourceRecordRow,
)


def _text(value: Any) -> str:
    return str(getattr(value, "value", value))


def health_of(row: tuple[Any, ...]) -> SourceHealth:
    """집계 행의 자리는 `health_statement`가 정한 순서 그대로다."""
    source, source_type, records, succeeded, failed, running, quarantined, rows, latest_at = row
    return SourceHealth(
        source=source,
        source_type=_text(source_type),
        records=records,
        succeeded=succeeded,
        failed=failed,
        running=running,
        quarantined=quarantined,
        rows=rows,
        latest_at=latest_at,
        latest_status=None,
    )


def build_health(
    rows: Sequence[tuple[Any, ...]], since: datetime, *, limit: int, offset: int, has_more: bool
) -> SourceHealthList:
    """**정렬은 조회문이 이미 했다** — 늦은 출처가 위다. 여기서 다시 정렬하면 쪽 안에서만 맞는다."""
    return SourceHealthList(
        items=tuple(health_of(row) for row in rows),
        since=since,
        limit=limit,
        offset=offset,
        has_more=has_more,
    )


def record_of(row: tuple[Any, ...]) -> SourceRecordRow:
    (
        record_id,
        source_type,
        source,
        source_key,
        started_at,
        completed_at,
        status,
        record_count,
        has_payload,
        has_metadata,
        payload_uri,
    ) = row
    return SourceRecordRow(
        id=record_id,
        source_type=_text(source_type),
        source=source,
        source_key=source_key,
        started_at=started_at,
        completed_at=completed_at,
        status=_text(status),
        record_count=record_count,
        has_payload=bool(has_payload),
        has_metadata=bool(has_metadata),
        payload_uri=payload_uri,
    )


def instrument_of(row: Any) -> InstrumentRow:
    return InstrumentRow(
        ticker=row.ticker,
        market=_text(row.market),
        name=row.name,
        kind=_text(row.kind),
        currency=row.currency,
        source_symbol=row.source_symbol,
        is_watched=row.is_watched,
    )


def session_of(row: Any) -> MarketSessionRow:
    return MarketSessionRow(
        market_code=_text(row.market_code),
        market_name=row.market_name,
        country_code=row.country_code,
        session_date=row.session_date,
        kis_business_day=row.kis_business_day,
        kis_trading_day=row.kis_trading_day,
        kis_open_day=row.kis_open_day,
        effective_open_day=row.effective_open_day,
        local_settlement_date=row.local_settlement_date,
        domestic_settlement_date=row.domestic_settlement_date,
        kis_weekday_code=row.kis_weekday_code,
        kis_settlement_day=row.kis_settlement_day,
        verified_at=row.verified_at,
        verified_by=None if row.verified_by is None else _text(row.verified_by),
    )


class CollectionReadService:
    """수집 원장과 마스터를 읽어 응답 계약으로 준다."""

    def __init__(self, repository: CollectionReadRepository) -> None:
        self._repository = repository

    async def health(
        self, since: datetime, *, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> SourceHealthList:
        rows, has_more = await self._repository.health_rows(since, limit=limit, offset=offset)
        return build_health(rows, since, limit=limit, offset=offset, has_more=has_more)

    async def records(
        self,
        *,
        start: datetime,
        end: datetime,
        sources: Sequence[str] = (),
        statuses: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> SourceRecordList:
        rows, has_more = await self._repository.record_rows(
            start=start,
            end=end,
            sources=sources,
            statuses=statuses,
            limit=limit,
            offset=offset,
        )
        return SourceRecordList(
            items=tuple(record_of(row) for row in rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    async def instruments(
        self, *, limit: int = DEFAULT_LIMIT, offset: int = 0
    ) -> InstrumentList:
        rows, has_more = await self._repository.instrument_rows(limit=limit, offset=offset)
        return InstrumentList(
            items=tuple(instrument_of(row) for row in rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    async def sessions(
        self,
        *,
        start: date,
        end: date,
        markets: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> MarketSessionList:
        rows, has_more = await self._repository.session_rows(
            start=start, end=end, markets=markets, limit=limit, offset=offset
        )
        return MarketSessionList(
            items=tuple(session_of(row) for row in rows),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )
