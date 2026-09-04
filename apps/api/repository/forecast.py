"""코스피 전망 조회. **조회 단위가 슬롯이다.**

세션 팩토리를 생성자로 받고 응답 하나가 세션 하나다. 다른 리포지토리와 같은 형태다.

**목록은 무거운 두 칸을 안 읽는다.** `reasons`·`input_state`가 JSONB로 수 KB씩이라
`load_only`로 뺀다 — 문서 목록에서 `select *`가 55KB 응답에 6.8MB를 옮긴 자리와 같은
실수다(20단계 §8.6.2).
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import load_only

from apps.api.repository.common import DEFAULT_LIMIT, RowBundle
from apps.models.analysis import KospiForecast, KospiSlot

# 목록이 쓰는 칸. **`reasons`와 `input_state`가 없다** — 상세만 그 둘을 읽는다.
LIST_COLUMNS = (
    KospiForecast.id,
    KospiForecast.run_date,
    KospiForecast.slot,
    KospiForecast.as_of_at,
    KospiForecast.base_price,
    KospiForecast.base_at,
    KospiForecast.so_far_pct,
    KospiForecast.direction,
    KospiForecast.expected_change_pct,
    KospiForecast.band_pct,
    KospiForecast.weak,
    KospiForecast.rejected_reasons,
    KospiForecast.actual_change_pct,
    KospiForecast.hit,
    KospiForecast.within_band,
    KospiForecast.graded_at,
    KospiForecast.prompt_version,
    KospiForecast.llm_model,
    KospiForecast.llm_run_id,
)

# 목록이 `reasons` 배열의 길이만 필요로 한다. 배열 자체를 옮기지 않는다.
REASON_COUNT = func.coalesce(func.jsonb_array_length(KospiForecast.reasons), 0)


class ForecastListRows(RowBundle):
    forecasts: tuple[KospiForecast, ...] = ()
    has_more: bool = False
    # 전망 id → 이유 수. 목록이 배열을 안 읽으므로 따로 센다.
    reason_counts: dict[int, int] = Field(default_factory=dict)


class AccuracyRow(BaseModel):
    """슬롯 하나의 집계. **행 모양을 여기서 못 박는다** — `Row`를 넘기면 칸 오타가 런타임까지 산다."""

    model_config = ConfigDict(frozen=True)

    slot: str
    graded: int = 0
    hits: int = 0
    within_band: int = 0
    error_sum: float | None = None
    pending: int = 0


class ForecastReadRepository:
    """전망을 읽는다. 쓰기 경로는 없다 — 채점 칸도 Airflow가 한 번만 채운다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # --- 조회문 (테스트가 컴파일해서 본다) ---------------------------------------

    @staticmethod
    def list_statement(
        *,
        run_from: date,
        run_to: date,
        slots: Sequence[str] = (),
        graded: bool | None = None,
        hit: bool | None = None,
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Select[Any]:
        """목록 조회문. **날짜 경계가 양끝 포함이다** — `run_date`가 이미 KST 날짜라
        시각 경계로 바꿀 것이 없다.

        정렬은 최신 날짜 먼저, 같은 날 안에서는 슬롯 시간 순이다.
        """
        statement = (
            select(KospiForecast)
            .options(load_only(*LIST_COLUMNS))
            .where(KospiForecast.run_date >= run_from, KospiForecast.run_date <= run_to)
        )
        if slots:
            statement = statement.where(KospiForecast.slot.in_(slots))
        if graded is not None:
            # 채점 넷은 함께 있거나 함께 없다(모델 CHECK). `graded_at` 하나로 가른다.
            statement = statement.where(
                KospiForecast.graded_at.is_not(None) if graded else KospiForecast.graded_at.is_(None)
            )
        if hit is not None:
            statement = statement.where(KospiForecast.hit.is_(hit))
        return (
            statement.order_by(
                KospiForecast.run_date.desc(),
                KospiForecast.as_of_at.asc(),
            )
            .limit(limit + 1)
            .offset(offset)
        )

    @staticmethod
    def reason_count_statement(forecast_ids: Sequence[int]) -> Select[Any]:
        """이유 수만. 배열을 옮기지 않으려고 DB가 센다."""
        return select(KospiForecast.id, REASON_COUNT).where(KospiForecast.id.in_(forecast_ids))

    @staticmethod
    def detail_statement(run_date: date, slot: str) -> Select[Any]:
        """자연키로 한 건. **`id`가 아니다** — 같은 슬롯이 두 주소를 갖지 않게 한다."""
        return select(KospiForecast).where(
            KospiForecast.run_date == run_date, KospiForecast.slot == slot
        )

    @staticmethod
    def accuracy_statement(*, run_from: date, run_to: date) -> Select[Any]:
        """슬롯별 채점 집계. **평균이 아니라 합과 건수를 가져온다** — 슬롯 합계를 다시
        만들 때 평균의 평균이 되지 않게 한다."""
        return (
            select(
                KospiForecast.slot,
                func.count().filter(KospiForecast.graded_at.is_not(None)).label("graded"),
                func.count().filter(KospiForecast.hit.is_(True)).label("hits"),
                func.count().filter(KospiForecast.within_band.is_(True)).label("within_band"),
                func.sum(
                    func.abs(KospiForecast.actual_change_pct - KospiForecast.expected_change_pct)
                ).label("error_sum"),
                func.count().filter(KospiForecast.graded_at.is_(None)).label("pending"),
            )
            .where(KospiForecast.run_date >= run_from, KospiForecast.run_date <= run_to)
            .group_by(KospiForecast.slot)
        )

    # --- 공개 조회 -----------------------------------------------------------

    async def list_rows(self, **filters: Any) -> ForecastListRows:
        """전망 목록 한 쪽. 왕복 둘이고 한 세션 안이다."""
        limit = filters.get("limit", DEFAULT_LIMIT)
        async with self._session_factory() as session:
            rows = list((await session.execute(self.list_statement(**filters))).scalars())
            has_more = len(rows) > limit
            found = rows[:limit]
            if not found:
                return ForecastListRows(has_more=has_more)
            counts = {
                forecast_id: count
                for forecast_id, count in await session.execute(
                    self.reason_count_statement([row.id for row in found])
                )
            }
        return ForecastListRows(
            forecasts=tuple(found), has_more=has_more, reason_counts=counts
        )

    async def detail_row(self, run_date: date, slot: str) -> KospiForecast | None:
        async with self._session_factory() as session:
            return (
                await session.execute(self.detail_statement(run_date, slot))
            ).scalar_one_or_none()

    async def accuracy_rows(self, *, run_from: date, run_to: date) -> tuple[AccuracyRow, ...]:
        """슬롯별 집계. 행이 없는 슬롯은 여기서 안 만든다 — 채우는 것은 서비스다."""
        async with self._session_factory() as session:
            rows = (
                await session.execute(self.accuracy_statement(run_from=run_from, run_to=run_to))
            ).all()
        return tuple(
            AccuracyRow(
                slot=str(row.slot),
                graded=row.graded or 0,
                hits=row.hits or 0,
                within_band=row.within_band or 0,
                error_sum=None if row.error_sum is None else float(row.error_sum),
                pending=row.pending or 0,
            )
            for row in rows
        )


# 슬롯 값을 라우트가 리터럴로 적지 않게 모델 Enum에서 뽑는다.
FORECAST_SLOTS: tuple[str, ...] = tuple(slot.value for slot in KospiSlot)
