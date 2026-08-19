"""실시간 수집이 쓰는 저장 계층. `apps/models`의 ORM으로만 쓴다.

`BaseRepository`와 같은 모양(session_factory 주입)이지만 읽기 전용이 아니라
잠정 봉 upsert와 세션 계보(source_record) 갱신을 커밋한다. 트랜잭션 규칙은
문서 10.4와 같다: 세션 레코드 선 커밋, flush마다 짧은 트랜잭션 하나, 종료 상태
별도 커밋. 연결 전체를 감싸는 장기 트랜잭션을 만들지 않는다.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import Insert, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.models.market import StockBar, StockInvestorTradeDaily
from apps.models.raw import SourceRecord, SourceStatus, SourceType

PROVIDER = "kis"
SOURCE_KEY = "kis_realtime"


def provisional_upsert(rows: Sequence[Mapping[str, Any]]) -> Insert:
    """WebSocket 잠정 봉 upsert. REST가 확정(is_final=true)한 행은 절대 되돌리지 않는다.

    "WebSocket 잠정 → REST 확정 → 늦은 WebSocket" 순서에서 REST 값이 유지된다(문서 5.2).
    가드에 걸러진 행은 rowcount에 잡히지 않아 부르는 쪽이 실제 반영 수를 셀 수 있다.
    REST 확정 경로는 Airflow의 `stock_bar/upsert.sql`이고 여기와 규칙이 짝을 이룬다.
    """
    statement = insert(StockBar).values(list(rows))
    excluded = statement.excluded
    return statement.on_conflict_do_update(
        constraint="uq_stock_bar_natural_key",
        set_={
            "open": excluded.open,
            "high": excluded.high,
            "low": excluded.low,
            "close": excluded.close,
            "volume": excluded.volume,
            "previous_close": excluded.previous_close,
            "ingest_method": "websocket",
            "is_final": False,
            "source_record_id": excluded.source_record_id,
            "updated_at": func.now(),
        },
        where=StockBar.is_final.is_(False),
    )


class RealtimeRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def open_session(self, started_at: datetime, metadata: dict[str, Any]) -> int:
        """running 세션 레코드를 만들고 즉시 커밋한다. 실패한 세션도 흔적이 남는다."""
        record = SourceRecord(
            source_type=SourceType.WEBSOCKET,
            source=PROVIDER,
            source_key=SOURCE_KEY,
            started_at=started_at,
            completed_at=None,
            status=SourceStatus.RUNNING,
            record_count=0,
            payload=None,
            source_metadata=metadata,
        )
        async with self._session_factory() as session:
            session.add(record)
            await session.commit()
            return record.id

    async def store_bars(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """잠정 봉을 upsert하고 실제 반영된 행 수를 돌려준다. 한 flush = 한 트랜잭션."""
        if not rows:
            return 0
        async with self._session_factory() as session:
            result = await session.execute(provisional_upsert(rows))
            await session.commit()
            return result.rowcount

    async def close_session(
        self,
        source_record_id: int,
        completed_at: datetime,
        status: SourceStatus,
        record_count: int,
        metadata: dict[str, Any],
    ) -> None:
        """running으로 시작한 세션 레코드를 종료 상태로 바꾼다(문서 10.1). payload는 안 건드린다."""
        async with self._session_factory() as session:
            await session.execute(
                update(SourceRecord)
                .where(SourceRecord.id == source_record_id)
                .values(
                    completed_at=completed_at,
                    status=status,
                    record_count=record_count,
                    source_metadata=metadata,
                    updated_at=func.now(),
                )
            )
            await session.commit()

    async def previous_close(self, stock_code: str, business_date: date) -> Decimal | None:
        """직전 거래일 확정 종가. 일별 DAG의 select_previous_close.sql과 같은 질의다.

        없으면 그 종목의 잠정 저장을 비활성한다 — 지어낸 분모보다 빈 구간이 낫다.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                select(StockInvestorTradeDaily.close_price)
                .where(
                    StockInvestorTradeDaily.provider == PROVIDER,
                    StockInvestorTradeDaily.stock_code == stock_code,
                    StockInvestorTradeDaily.business_date < business_date,
                )
                .order_by(StockInvestorTradeDaily.business_date.desc())
                .limit(1)
            )
            value = result.scalar_one_or_none()
            return Decimal(value) if value is not None else None
