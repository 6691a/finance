"""추론 기록 조회. **세션 팩토리를 생성자로 받는다.**

`apps/realtime/repository.py`와 같은 모양이다 — 팩토리를 쥐고 조회 단위로 열고 닫는다.
세션 하나가 아니라 팩토리인 이유는 이 객체가 요청보다 오래 살 수 있어서다.

`airflow/sql/`은 Airflow 전용 규약이라(`modules/sql.py`가 `AIRFLOW_HOME` 아래를 읽는다)
공유하지 않는다. 두 번째 SQL 트리를 만들면 두 번째 `read_sql`과 "SQL 컬럼 vs 모델
metadata 대조" 테스트를 또 짜야 하는데 ORM은 그것을 공짜로 준다.

## `selectinload`를 쓰지 않는다

`apps/models/analysis/thesis.py`에 `relationship()`이 하나도 없다. 붙이면 한 줄로 끝나지만
그 파일은 마이그레이션의 원본이라 **조회 편의로 건드리는 자리가 아니다.** 대신
`WHERE thesis_id = ANY(:ids)` 배치 조회로 읽고 파이썬이 그룹핑한다 —
`airflow/sql/postgres/*/select_by_thesis_ids.sql`이 이미 같은 계약이다.

**응답 하나가 세션 하나다.** 상세는 조회 여섯이지만 전부 한 세션 안이다. 메서드마다
세션을 열면 한 응답이 커넥션을 여섯 번 빌린다.
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from pydantic import Field
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.api.repository.common import DEFAULT_LIMIT, RowBundle
from apps.models.analysis import (
    Thesis,
    ThesisEvidence,
    ThesisLlmRun,
    ThesisOutcome,
    ThesisPrecedent,
)


class ThesisListRows(RowBundle):
    theses: tuple[Thesis, ...] = ()
    has_more: bool = False
    # 추론 id → (채점 지평 수, 해설 지평 수, 평균 Brier)
    grades: dict[int, tuple[int, int, float | None]] = Field(default_factory=dict)


class ThesisDetailRows(RowBundle):
    thesis: Thesis
    citations: tuple[ThesisEvidence, ...] = ()
    outcomes: tuple[ThesisOutcome, ...] = ()
    # **나가는 엣지만**이다. "이 판단이 무엇을 보고 나왔나"가 상세의 질문이고,
    # 들어오는 쪽("누가 나를 참고했나")은 그래프가 답한다.
    precedent_ids: tuple[int, ...] = ()
    neighbours: dict[int, Thesis] = Field(default_factory=dict)
    runs: dict[int, ThesisLlmRun] = Field(default_factory=dict)


class ThesisGraphRows(RowBundle):
    center: Thesis
    citations: tuple[ThesisEvidence, ...] = ()
    edges: tuple[ThesisPrecedent, ...] = ()
    neighbours: dict[int, Thesis] = Field(default_factory=dict)
    # 그래프 노드에 실을 채점. **지평 0이다**(4-graph.md 2절 각주).
    grades: dict[int, ThesisOutcome] = Field(default_factory=dict)


class ThesisReadRepository:
    """추론 기록을 읽는다. 쓰기 경로는 없다."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    # --- 조회문 (테스트가 컴파일해서 본다) ---------------------------------------

    @staticmethod
    def list_statement(
        *,
        run_date_from: date,
        run_date_to: date,
        run_slots: Sequence[str] = (),
        subject_codes: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> Select[Any]:
        """목록 조회문. **정렬 키가 `as_of_at`이다.**

        `run_slot`으로 정렬하면 안 된다 — 문자열이라 `intraday_afternoon` →
        `intraday_midday` → `intraday_morning` → `post_close` → `pre_close` → `pre_open`
        순이 되어 **시간이 뒤집힌다.** 슬롯의 진짜 시간 키는 `as_of_at`이다.

        `limit + 1`을 읽는다. 총 건수를 세지 않고 다음 쪽이 있는지만 본다.
        """
        statement = select(Thesis).where(
            Thesis.run_date >= run_date_from,
            Thesis.run_date <= run_date_to,
        )
        if run_slots:
            statement = statement.where(Thesis.run_slot.in_(run_slots))
        if subject_codes:
            statement = statement.where(Thesis.subject_code.in_(subject_codes))
        return (
            statement.order_by(
                Thesis.as_of_at.desc(),
                Thesis.subject_kind,
                Thesis.subject_code,
                Thesis.id,
            )
            .limit(limit + 1)
            .offset(offset)
        )

    @staticmethod
    def outcome_summary_statement(thesis_ids: Sequence[int]) -> Select[Any]:
        """목록에 붙일 평가 요약. 지평별 행을 추론 하나로 접는다."""
        return (
            select(
                ThesisOutcome.thesis_id,
                func.count(ThesisOutcome.evaluated_at).label("graded"),
                func.count(ThesisOutcome.narrative).label("narrated"),
                func.avg(ThesisOutcome.brier_score).label("mean_brier"),
            )
            .where(ThesisOutcome.thesis_id.in_(thesis_ids))
            .group_by(ThesisOutcome.thesis_id)
        )

    # --- 공개 조회 -----------------------------------------------------------

    async def list_rows(
        self,
        *,
        run_date_from: date,
        run_date_to: date,
        run_slots: Sequence[str] = (),
        subject_codes: Sequence[str] = (),
        limit: int = DEFAULT_LIMIT,
        offset: int = 0,
    ) -> ThesisListRows:
        """추론 목록 한 쪽. 왕복 둘이고 한 세션 안이다."""
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    self.list_statement(
                        run_date_from=run_date_from,
                        run_date_to=run_date_to,
                        run_slots=run_slots,
                        subject_codes=subject_codes,
                        limit=limit,
                        offset=offset,
                    )
                )
            ).scalars()
            found = list(rows)
            has_more = len(found) > limit
            found = found[:limit]
            if not found:
                return ThesisListRows()
            grades = {
                row.thesis_id: (
                    row.graded,
                    row.narrated,
                    None if row.mean_brier is None else float(row.mean_brier),
                )
                for row in await session.execute(
                    self.outcome_summary_statement([thesis.id for thesis in found])
                )
            }
        return ThesisListRows(theses=tuple(found), has_more=has_more, grades=grades)

    async def detail_rows(self, thesis_id: int) -> ThesisDetailRows | None:
        """상세에 필요한 행 전부. **한 세션 안에서 여섯 번 묻는다.**"""
        async with self._session_factory() as session:
            thesis = await self._thesis(session, thesis_id)
            if thesis is None:
                return None
            citations = await self._evidence(session, [thesis_id])
            outcomes = await self._outcomes(session, [thesis_id])
            edges = await self._precedents(session, [thesis_id])
            precedent_ids = [edge.precedent_id for edge in edges if edge.thesis_id == thesis_id]
            neighbours = await self._theses_by_id(session, precedent_ids)
            runs = await self._llm_runs(
                session,
                [thesis.llm_run_id, *[row.narration_run_id for row in outcomes]],
            )
        return ThesisDetailRows(
            thesis=thesis,
            citations=tuple(citations),
            outcomes=tuple(outcomes),
            precedent_ids=tuple(precedent_ids),
            neighbours=neighbours,
            runs=runs,
        )

    async def graph_rows(self, thesis_id: int) -> ThesisGraphRows | None:
        """이웃 그래프에 필요한 행 전부. 1홉이다."""
        async with self._session_factory() as session:
            thesis = await self._thesis(session, thesis_id)
            if thesis is None:
                return None
            edges = await self._precedents(session, [thesis_id])
            neighbour_ids = {edge.precedent_id for edge in edges} | {edge.thesis_id for edge in edges}
            neighbour_ids.discard(thesis_id)
            neighbours = await self._theses_by_id(session, sorted(neighbour_ids))
            citations = await self._evidence(session, [thesis_id])
            grades = await self._zero_horizon_grades(session, [thesis_id, *neighbours])
        return ThesisGraphRows(
            center=thesis,
            citations=tuple(citations),
            edges=tuple(edges),
            neighbours=neighbours,
            grades=grades,
        )

    # --- 조각 조회 -----------------------------------------------------------

    @staticmethod
    async def _thesis(session: AsyncSession, thesis_id: int) -> Thesis | None:
        return (await session.execute(select(Thesis).where(Thesis.id == thesis_id))).scalar_one_or_none()

    @staticmethod
    async def _evidence(session: AsyncSession, thesis_ids: Sequence[int]) -> list[ThesisEvidence]:
        """원 추론과 해설의 인용이 함께 온다. 부르는 쪽이 `outcome_horizon_days`로 가른다 —
        한 왕복으로 끝내려는 것이다."""
        if not thesis_ids:
            return []
        statement = (
            select(ThesisEvidence)
            .where(ThesisEvidence.thesis_id.in_(thesis_ids))
            .order_by(ThesisEvidence.thesis_id, ThesisEvidence.outcome_horizon_days, ThesisEvidence.rank)
        )
        return list((await session.execute(statement)).scalars())

    @staticmethod
    async def _outcomes(session: AsyncSession, thesis_ids: Sequence[int]) -> list[ThesisOutcome]:
        if not thesis_ids:
            return []
        statement = (
            select(ThesisOutcome)
            .where(ThesisOutcome.thesis_id.in_(thesis_ids))
            .order_by(ThesisOutcome.thesis_id, ThesisOutcome.horizon_days)
        )
        return list((await session.execute(statement)).scalars())

    @staticmethod
    async def _precedents(session: AsyncSession, thesis_ids: Sequence[int]) -> list[ThesisPrecedent]:
        """**나가는 것과 들어오는 것 양쪽이다.** 나가는 쪽만 주면 "이 판단을 누가
        참고했나"를 못 본다. `precedent_id` 단독 인덱스가 그 반대 방향을 받는다."""
        if not thesis_ids:
            return []
        statement = select(ThesisPrecedent).where(
            or_(
                ThesisPrecedent.thesis_id.in_(thesis_ids),
                ThesisPrecedent.precedent_id.in_(thesis_ids),
            )
        )
        return list((await session.execute(statement)).scalars())

    @staticmethod
    async def _theses_by_id(session: AsyncSession, thesis_ids: Sequence[int]) -> dict[int, Thesis]:
        """이웃 추론의 라벨·확률을 채운다. 엣지만으로는 그릴 수 없다."""
        if not thesis_ids:
            return {}
        rows = (await session.execute(select(Thesis).where(Thesis.id.in_(thesis_ids)))).scalars()
        return {thesis.id: thesis for thesis in rows}

    @staticmethod
    async def _llm_runs(session: AsyncSession, run_ids: Sequence[int | None]) -> dict[int, ThesisLlmRun]:
        """대화 요약. 상세가 id만 잇고 툴 배열은 복제하지 않는다."""
        ids = [value for value in run_ids if value is not None]
        if not ids:
            return {}
        rows = (await session.execute(select(ThesisLlmRun).where(ThesisLlmRun.id.in_(ids)))).scalars()
        return {run.id: run for run in rows}

    @staticmethod
    async def _zero_horizon_grades(session: AsyncSession, thesis_ids: Sequence[int]) -> dict[int, ThesisOutcome]:
        """그래프 노드에 실을 채점. **지평 0이다**(4-graph.md 2절 각주).

        채점된 추론이면 항상 있는 유일한 지평이라 노드 모양이 시간에 따라 변하지 않는다.
        """
        if not thesis_ids:
            return {}
        statement = select(ThesisOutcome).where(
            and_(ThesisOutcome.thesis_id.in_(thesis_ids), ThesisOutcome.horizon_days == 0)
        )
        return {row.thesis_id: row for row in (await session.execute(statement)).scalars()}
