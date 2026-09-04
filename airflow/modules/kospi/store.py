"""Postgres 읽기·쓰기 — 봉, 전망, 채점, LLM 원장.

**연결 하나가 객체 하나다.** 트랜잭션 경계는 이 객체가 쥔다 — 원장은 모델 호출 전에 따로
커밋해야 하고 전망 저장은 그것과 다른 트랜잭션이라, 부르는 쪽이 `atomic`을 매번 감싸면
그 구분이 흩어진다.

**관계와 메모는 여기 없다.** 그쪽 원본은 Neo4j이고 `kospi/graph.py`가 갖는다.

쿼리는 전부 `airflow/sql/postgres/kospi_*/`의 파일이다. 파이썬 문자열로 두지 않는다.
"""

import json
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from modules.db import TransactionalConnection
from modules.kospi.domain import (
    BARS_WINDOW,
    INDEX_CODE,
    INDEX_PROVIDER,
    MIN_MOVE_BASELINE_BARS,
    MOVE_BASELINE_BARS,
    Direction,
    KospiError,
    RunSlot,
    ToolCallRecord,
)
from modules.kospi.state import DailyBar, MoveBaseline
from modules.sql import read_sql
from modules.usage import TokenUsage
from modules.utility import atomic

logger = logging.getLogger(__name__)

SELECT_BARS = read_sql("postgres", "kospi_forecast", "select_bars.sql")
SELECT_PREVIOUS_CLOSE = read_sql("postgres", "kospi_forecast", "select_previous_close.sql")
SELECT_INTRADAY_BAR = read_sql("postgres", "kospi_forecast", "select_intraday_bar.sql")
SELECT_SESSION_CLOSE = read_sql("postgres", "kospi_forecast", "select_session_close.sql")
SELECT_FLOW_LATEST = read_sql("postgres", "kospi_forecast", "select_flow_latest.sql")
SELECT_MOVE_SIZES = read_sql("postgres", "kospi_forecast", "select_move_sizes.sql")
INSERT_FORECAST = read_sql("postgres", "kospi_forecast", "insert.sql")
SELECT_BY_DATE = read_sql("postgres", "kospi_forecast", "select_by_date.sql")
UPDATE_GRADE = read_sql("postgres", "kospi_forecast", "update_grade.sql")
SELECT_PENDING_GRADES = read_sql("postgres", "kospi_forecast", "select_pending_grades.sql")
INSERT_LLM_RUN = read_sql("postgres", "kospi_llm_run", "insert.sql")
FINISH_LLM_RUN = read_sql("postgres", "kospi_llm_run", "finish.sql")
INSERT_TOOL_CALL = read_sql("postgres", "kospi_llm_run", "insert_tool_call.sql")

# 한 번에 회수하는 미채점 전망 수. 하루 셋이라 정상이면 한 자리인데, 장후 DAG가 며칠 죽어
# 있었으면 밀린 만큼 는다.
MAX_PENDING_GRADES = 60


class SessionQuote(BaseModel):
    """장중 슬롯의 현재가와 그날 지금까지."""

    model_config = ConfigDict(frozen=True)

    bar_at: datetime
    close: Decimal
    previous_close: Decimal | None = None
    session_open: Decimal | None = None
    session_high: Decimal | None = None
    session_low: Decimal | None = None


class MarketFlow(BaseModel):
    """오늘 그 시각까지의 시장 단위 누적 순매수(주).

    **금액을 담지 않는다.** `market_investor_flow_snapshot`의 금액 칸은 모델 주석이 "단위
    미확정"이라 이름을 붙이면 거짓이 된다. 금액이 필요해지면 단위를 먼저 확인한다.
    """

    model_config = ConfigDict(frozen=True)

    observed_at: datetime
    foreign_net_buy_qty: int | None = None
    institution_net_buy_qty: int | None = None
    individual_net_buy_qty: int | None = None


class StoredForecast(BaseModel):
    """저장된 전망 하나. 채점 칸은 아직 비어 있을 수 있다."""

    model_config = ConfigDict(frozen=True)

    id: int
    run_date: date
    slot: RunSlot
    as_of_at: datetime
    base_price: Decimal
    base_at: datetime
    so_far_pct: Decimal | None
    direction: Direction
    expected_change_pct: Decimal
    band_pct: Decimal
    reasons: tuple[dict[str, Any], ...]
    weak: bool
    rejected_reasons: int
    actual_change_pct: Decimal | None = None
    hit: bool | None = None
    within_band: bool | None = None
    graded_at: datetime | None = None
    prompt_version: str = ""
    llm_model: str = ""


class PendingGrade(BaseModel):
    """아직 채점하지 않은 전망 하나."""

    model_config = ConfigDict(frozen=True)

    id: int
    run_date: date
    slot: RunSlot
    base_price: Decimal
    direction: Direction
    expected_change_pct: Decimal
    band_pct: Decimal


class KospiStore:
    """연결 하나를 쥐고 조회·저장을 한다."""

    def __init__(self, connection: TransactionalConnection) -> None:
        self._connection = connection

    # --- 관측 상태 --------------------------------------------------------

    def bars(self, *, as_of_at: datetime, before_date: date, limit: int = BARS_WINDOW) -> tuple[DailyBar, ...]:
        """확정 일봉 창. **모자라면 부르는 쪽이 죽인다** — 여기서 빈 봉을 채우지 않는다."""
        rows = self._fetch(
            SELECT_BARS,
            {
                "provider": INDEX_PROVIDER,
                "symbol": INDEX_CODE,
                "before_date": before_date,
                "as_of_at": as_of_at,
                "limit": limit,
            },
        )
        return tuple(
            DailyBar(business_date=row[0], open=row[1], close=row[2], change_pct=row[3]) for row in rows
        )

    def move_baseline(self, *, as_of_at: datetime, before_date: date) -> MoveBaseline:
        """크기 기준선. **표본이 모자라면 칸을 전부 비운다.**

        0으로 채우면 모델이 그 숫자를 쓴다. `MIN_MOVE_BASELINE_BARS` 미만이면 `observations`
        만 담아 "얼마나 얇은지"를 프롬프트가 보이게 한다.
        """
        rows = self._fetch(
            SELECT_MOVE_SIZES,
            {
                "provider": INDEX_PROVIDER,
                "symbol": INDEX_CODE,
                "before_date": before_date,
                "as_of_at": as_of_at,
                "limit": MOVE_BASELINE_BARS,
            },
        )
        if not rows:
            return MoveBaseline()
        row = rows[0]
        observations = int(row[0] or 0)
        if observations < MIN_MOVE_BASELINE_BARS:
            logger.warning("크기 기준선 표본이 %s봉뿐이라 칸을 비운다", observations)
            return MoveBaseline(observations=observations)
        up_days = int(row[7] or 0)
        return MoveBaseline(
            observations=observations,
            abs_p25=row[1],
            abs_p50=row[2],
            abs_p75=row[3],
            abs_p90=row[4],
            up_median=row[5],
            down_median=row[6],
            up_day_ratio=(Decimal(up_days) / Decimal(observations)).quantize(Decimal("0.01")),
        )

    def previous_close(self, *, as_of_at: datetime, before_date: date) -> tuple[date, Decimal] | None:
        """장전 슬롯의 기준가. 앞의 마지막 확정 종가와 그 날짜."""
        rows = self._fetch(
            SELECT_PREVIOUS_CLOSE,
            {
                "provider": INDEX_PROVIDER,
                "symbol": INDEX_CODE,
                "before_date": before_date,
                "as_of_at": as_of_at,
            },
        )
        if not rows:
            return None
        return rows[0][0], Decimal(rows[0][1])

    def intraday_quote(self, *, as_of_at: datetime, session_start: datetime) -> SessionQuote | None:
        """장중 슬롯의 현재가. 0건이면 `None`이고 부르는 쪽이 준비 검사로 다룬다."""
        rows = self._fetch(
            SELECT_INTRADAY_BAR,
            {
                "provider": INDEX_PROVIDER,
                "symbol": INDEX_CODE,
                "as_of_at": as_of_at,
                "session_start": session_start,
            },
        )
        if not rows:
            return None
        row = rows[0]
        return SessionQuote(
            bar_at=row[0],
            close=row[1],
            previous_close=row[2],
            session_open=row[3],
            session_high=row[4],
            session_low=row[5],
        )

    def market_flow(self, *, as_of_at: datetime, session_start: datetime) -> "MarketFlow | None":
        """오늘 그 시각까지의 투자자별 누적 순매수. 장중 슬롯의 관측 상태에 실린다.

        **0건은 실패가 아니다.** 개장 직후나 스냅샷 수집이 밀린 순간이 있고, 그때는 그 칸이
        비는 것이 맞다 — 0으로 채우면 "안 샀다"가 된다.
        """
        rows = self._fetch(
            SELECT_FLOW_LATEST,
            {"market_code": INDEX_CODE, "as_of_at": as_of_at, "session_start": session_start},
        )
        if not rows:
            return None
        row = rows[0]
        return MarketFlow(
            observed_at=row[0],
            foreign_net_buy_qty=row[1],
            institution_net_buy_qty=row[2],
            individual_net_buy_qty=row[3],
        )

    def session_close(self, business_date: date) -> Decimal | None:
        """그날 확정 종가. 없으면 `None`이고 **0으로 꾸미지 않는다.**"""
        rows = self._fetch(
            SELECT_SESSION_CLOSE,
            {"provider": INDEX_PROVIDER, "symbol": INDEX_CODE, "business_date": business_date},
        )
        return Decimal(rows[0][1]) if rows else None

    # --- 전망 -------------------------------------------------------------

    def forecasts(self, run_date: date) -> tuple[StoredForecast, ...]:
        """그날 전망 전부(슬롯 시간 순). 재실행 판정·앞 슬롯 참조·장후 관찰이 같이 쓴다."""
        rows = self._fetch(SELECT_BY_DATE, {"run_date": run_date})
        return tuple(
            StoredForecast(
                id=row[0],
                run_date=row[1],
                slot=RunSlot(row[2]),
                as_of_at=row[3],
                base_price=row[4],
                base_at=row[5],
                so_far_pct=row[6],
                direction=Direction(row[7]),
                expected_change_pct=row[8],
                band_pct=row[9],
                reasons=tuple(row[10] or ()),
                weak=bool(row[11]),
                rejected_reasons=int(row[12] or 0),
                actual_change_pct=row[13],
                hit=row[14],
                within_band=row[15],
                graded_at=row[16],
                prompt_version=row[17] or "",
                llm_model=row[18] or "",
            )
            for row in rows
        )

    def store_forecast(
        self,
        *,
        run_date: date,
        slot: RunSlot,
        as_of_at: datetime,
        base_price: Decimal,
        base_at: datetime,
        so_far_pct: Decimal | None,
        direction: Direction,
        expected_change_pct: Decimal,
        band_pct: Decimal,
        reasons: Sequence[dict[str, Any]],
        weak: bool,
        rejected_reasons: int,
        input_state: dict[str, Any],
        prompt_version: str,
        llm_model: str,
        dag_run_id: str,
        llm_run_id: int | None,
    ) -> int | None:
        """전망 하나를 쓴다. 이미 있으면 `None`이다(첫 성공본 불변).

        **`input_state`를 통째로 남긴다.** 관계 표와 메모는 그래프가 원본인데 그쪽은 다음 날
        바뀐다 — 이 행이 없으면 "그 전망이 무엇을 보고 나왔나"를 되짚을 수 없다.
        """
        with atomic(self._connection) as transaction, transaction.cursor() as cursor:
            cursor.execute(
                INSERT_FORECAST,
                {
                    "run_date": run_date,
                    "slot": slot.value,
                    "as_of_at": as_of_at,
                    "base_price": base_price,
                    "base_at": base_at,
                    "so_far_pct": so_far_pct,
                    "direction": direction.value,
                    "expected_change_pct": expected_change_pct,
                    "band_pct": band_pct,
                    "reasons": json.dumps(list(reasons), ensure_ascii=False),
                    "weak": weak,
                    "rejected_reasons": rejected_reasons,
                    "input_state": json.dumps(input_state, ensure_ascii=False),
                    "prompt_version": prompt_version,
                    "llm_model": llm_model,
                    "dag_run_id": dag_run_id,
                    "llm_run_id": llm_run_id,
                },
            )
            row = cursor.fetchone()
        return int(row[0]) if row else None

    # --- 채점 -------------------------------------------------------------

    def pending_grades(self, *, run_date: date, limit: int = MAX_PENDING_GRADES) -> tuple[PendingGrade, ...]:
        """아직 채점하지 않은 전망. 날짜 상한이 없어 실패한 날도 회수된다."""
        rows = self._fetch(SELECT_PENDING_GRADES, {"run_date": run_date, "limit": limit})
        return tuple(
            PendingGrade(
                id=row[0],
                run_date=row[1],
                slot=RunSlot(row[2]),
                base_price=row[3],
                direction=Direction(row[4]),
                expected_change_pct=row[5],
                band_pct=row[6],
            )
            for row in rows
        )

    def store_grade(
        self,
        *,
        run_date: date,
        slot: RunSlot,
        actual_change_pct: Decimal,
        hit: bool,
        within_band: bool,
    ) -> bool:
        """채점 결과를 쓴다. 이미 채점된 행이면 `False`이고 아무 것도 바꾸지 않는다."""
        with atomic(self._connection) as transaction, transaction.cursor() as cursor:
            cursor.execute(
                UPDATE_GRADE,
                {
                    "run_date": run_date,
                    "slot": slot.value,
                    "actual_change_pct": actual_change_pct,
                    "hit": hit,
                    "within_band": within_band,
                    "graded_at": datetime.now(UTC),
                },
            )
            return cursor.fetchone() is not None

    # --- 원장 -------------------------------------------------------------

    def start_llm_run(
        self,
        *,
        kind: str,
        run_date: date,
        slot: RunSlot | None,
        as_of_at: datetime,
        llm_model: str,
        prompt_version: str,
        dag_run_id: str,
        try_number: int,
    ) -> int:
        """대화 하나를 `running`으로 열고 그 id를 준다. **그래프를 부르기 전에 커밋한다.**

        대화가 죽어도 "시작했다"는 사실이 남아야 한다. 실패한 대화가 원장에 없으면 패턴
        분석이 성공한 실행만 보게 된다.

        전망 저장과 **다른 트랜잭션이다.** 원장이 못 써졌다고 전망을 버리면 안 되고, 전망
        저장이 실패해도 "무엇을 봤나"는 남아야 한다.
        """
        with atomic(self._connection) as transaction, transaction.cursor() as cursor:
            cursor.execute(
                INSERT_LLM_RUN,
                {
                    "kind": kind,
                    "run_date": run_date,
                    "slot": slot.value if slot else None,
                    "as_of_at": as_of_at,
                    "llm_model": llm_model,
                    "prompt_version": prompt_version,
                    "dag_run_id": dag_run_id,
                    "try_number": try_number,
                    "started_at": datetime.now(UTC),
                },
            )
            row = cursor.fetchone()
        if row is None:
            raise KospiError("failed to open an llm run ledger row")
        return int(row[0])

    def finish_llm_run(
        self,
        llm_run_id: int,
        *,
        status: str,
        records: Sequence[ToolCallRecord],
        tool_rounds: int,
        truncated: bool,
        rejected: int,
        observations: int | None = None,
        memories: dict[str, int] | None = None,
        usage: TokenUsage | None = None,
        error: str | None = None,
    ) -> None:
        """대화를 닫고 그 안의 툴 호출을 한 트랜잭션에 쓴다.

        `usage`는 그래프 밖 콜백이 누적한 값이라 **실패한 대화에도 값이 있다.** 안 주면
        토큰 넷이 NULL이고 그건 "안 쟀다"는 뜻이다 — 모델을 못 부르고 죽은 대화는 0이다.
        """
        counts = memories or {}
        delivered_chars = sum(record.result_chars for record in records if record.delivered)
        with atomic(self._connection) as transaction, transaction.cursor() as cursor:
            cursor.execute(
                FINISH_LLM_RUN,
                {
                    "id": llm_run_id,
                    "status": status,
                    "finished_at": datetime.now(UTC),
                    "error": error,
                    "tool_rounds": tool_rounds,
                    "tool_calls": len(records),
                    "tool_result_chars": delivered_chars,
                    "truncated": truncated,
                    "rejected": rejected,
                    "observations_written": observations,
                    "memories_written": counts.get("written"),
                    "memories_rejected": counts.get("rejected"),
                    "memories_kept": counts.get("kept"),
                    "memories_dropped": counts.get("dropped"),
                    "memories_unreviewed": counts.get("unreviewed"),
                    "memories_expired": counts.get("expired"),
                    "prompt_tokens": usage.prompt if usage else None,
                    "cached_tokens": usage.cached if usage else None,
                    "completion_tokens": usage.completion if usage else None,
                    "reasoning_tokens": usage.reasoning if usage else None,
                },
            )
            for record in records:
                cursor.execute(
                    INSERT_TOOL_CALL,
                    {
                        "llm_run_id": llm_run_id,
                        "seq": record.seq,
                        "round_no": record.round_no,
                        "tool_call_id": record.tool_call_id,
                        "tool_name": record.tool_name,
                        "arguments": json.dumps(record.arguments, ensure_ascii=False, default=str),
                        "validated_arguments": (
                            None
                            if record.validated_arguments is None
                            else json.dumps(record.validated_arguments, ensure_ascii=False, default=str)
                        ),
                        "requested_at": record.requested_at,
                        "duration_ms": record.duration_ms,
                        "result": record.result,
                        "result_chars": record.result_chars,
                        "error_kind": record.error_kind.value if record.error_kind else None,
                        "error": record.error,
                        "delivered": record.delivered,
                    },
                )

    # --- 공통 -------------------------------------------------------------

    def _fetch(self, statement: str, parameters: dict[str, Any]) -> list[Sequence[Any]]:
        with self._connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            return list(cursor.fetchall())
