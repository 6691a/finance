"""기대와 실제를 대조해 서프라이즈를 판정한다 — 원장과 Slack 표현.

**LLM이 없다.** 실제값 확보 → 발표 전 기대 집계 → 분류 → INSERT가 전부 결정론이고 수식은
`expectation/domain`의 순수 함수가 갖는다. 그래서 이 모듈은 LangChain을 끌고 오지 않는다.

`ExpectationStore`가 연결 하나를 쥔다. 조회·저장·판정이 같은 원장을 보므로 층마다 store를
따로 두지 않는다.

Slack 표현은 여기 함께 둔다. 서른 줄짜리라 모듈 하나를 낼 값어치가 없고, `JudgedOutcome`
하나만 받는 순수 변환이라 상태도 없다.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from modules.db import Connection, Cursor
from modules.expectation.domain import (
    DEFAULT_BATCH_SIZE,
    EVENT_LABELS,
    METRIC_LABELS,
    PROMPT_VERSION,
    VERDICT_LABELS,
    ClaimRow,
    EarningsFactRow,
    NormalizedClaim,
    PendingExtractionDocument,
    aggregate_expectations,
    classify_surprise,
    format_krw,
    period_end_for,
    resolve_actual,
    resolve_earnings_actual,
)
from modules.sql import read_sql

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 저장과 판정 — SQL은 행을 나르고 판단은 위의 순수 함수가 한다.
# ---------------------------------------------------------------------------

PENDING_EXTRACTION = read_sql("postgres", "document", "select_pending_extraction.sql")
CLAIM_INSERT = read_sql("postgres", "stock_event_claim", "insert.sql")
EXTRACTION_UPSERT = read_sql("postgres", "stock_event_extraction", "upsert.sql")
PENDING_JUDGMENT = read_sql("postgres", "stock_event_claim", "select_pending_judgment.sql")
PENDING_EARNINGS_EXPECTATIONS = read_sql("postgres", "stock_event_claim", "select_pending_earnings_expectations.sql")
EARNINGS_ACTUAL = read_sql("postgres", "earnings_fact", "select_actual_for_judgment.sql")
OUTCOME_INSERT = read_sql("postgres", "stock_event_outcome", "insert.sql")


class JudgedOutcome(BaseModel):
    """이번 실행이 새로 쓴 판정 하나. Slack 렌더링의 입력이다."""

    model_config = ConfigDict(frozen=True)

    stock_code: str
    event_type: str
    period_key: str
    metric: str
    expected_value: Decimal
    expectation_count: int
    actual_value: Decimal
    surprise_pct: Decimal
    verdict: str
    announced_at: datetime


def _claim_rows(rows: Sequence[Sequence[Any]]) -> tuple[ClaimRow, ...]:
    return tuple(
        ClaimRow(
            stock_code=row[0],
            event_type=row[1],
            period_key=row[2],
            metric=row[3],
            claim_kind=row[4],
            value=row[5],
            stated_at=row[6],
            broker=row[7],
            document_id=row[8],
            source_record_id=row[9],
        )
        for row in rows
    )


def _group_by_event(rows: Sequence[ClaimRow]) -> dict[tuple[str, str, str, str], list[ClaimRow]]:
    grouped: dict[tuple[str, str, str, str], list[ClaimRow]] = {}
    for row in rows:
        grouped.setdefault((row.stock_code, row.event_type, row.period_key, row.metric), []).append(row)
    return grouped


class ExpectationStore:
    """기대·판정 원장을 읽고 쓴다. **연결과 프롬프트 버전이 상태다.**

    전에는 조회·저장·판정 셋이 각각 `connection`을 받고 그중 둘이 `prompt_version`까지
    다시 받았다. `assessment.AssessmentStore`와 같은 모양이다 — 문서 하나가 트랜잭션
    하나라 DAG이 저장마다 새 연결을 열고, 그 연결의 수명이 이 객체의 수명이다.

    `dag_run_id`는 생성자가 아니라 `judge`의 인자다. 판정 한 번에만 쓰이고 조회·저장과
    상관이 없다.
    """

    def __init__(self, connection: Connection, prompt_version: str = PROMPT_VERSION) -> None:
        self._connection = connection
        self._prompt_version = prompt_version

    def pending(self, limit: int = DEFAULT_BATCH_SIZE) -> tuple[PendingExtractionDocument, ...]:
        """추출을 기다리는 문서. 평가 완료 + 종목 태그 + (미추출이거나 본문·프롬프트가 바뀜)."""
        with self._connection.cursor() as cursor:
            cursor.execute(PENDING_EXTRACTION, (self._prompt_version, limit))
            rows = cursor.fetchall()
        return tuple(
            PendingExtractionDocument(
                id=row[0],
                source_slug=row[1],
                title=row[2],
                summary=row[3],
                body=row[4],
                published_at=row[5],
                detected_at=row[6],
                content_hash=row[7],
                tickers=tuple(row[8] or ()),
            )
            for row in rows
        )

    def store_extraction(
        self,
        document: PendingExtractionDocument,
        claims: Sequence[NormalizedClaim],
        model: str,
        extracted_at: datetime | None = None,
    ) -> None:
        """주장과 원장을 저장한다. 문서 하나가 트랜잭션 하나다(커밋은 호출자가 한다).

        주장 0건도 원장에 남는다 — "뽑았는데 없었다"와 "아직 안 뽑았다"가 구분돼야
        매시간 같은 문서를 다시 뽑지 않는다.
        """
        stated_at = document.stated_at
        with self._connection.cursor() as cursor:
            for claim in claims:
                cursor.execute(
                    CLAIM_INSERT,
                    (
                        claim.stock_code,
                        claim.event_type,
                        claim.period_key,
                        claim.metric,
                        claim.claim_kind,
                        claim.value,
                        claim.value_low,
                        claim.value_high,
                        stated_at,
                        claim.broker,
                        document.id,
                        None,
                    ),
                )
            cursor.execute(
                EXTRACTION_UPSERT,
                (
                    document.id,
                    document.content_hash,
                    extracted_at or datetime.now(UTC),
                    model,
                    self._prompt_version,
                    len(claims),
                ),
            )

    def judge(self, dag_run_id: str) -> tuple[JudgedOutcome, ...]:
        """판정 없는 이벤트를 대조해 새 판정 행을 쓴다. 이번 실행이 **새로 쓴** 것만 돌려준다.

        LLM이 없다. 실제값 확보(주장 일치 또는 earnings_fact) → 발표 전 기대 집계 → 분류 →
        INSERT(첫 성공본 불변, RETURNING 0행이면 동시 실행이 먼저 쓴 것이라 발송 대상이 아니다).
        조건을 못 채운 키(실제 불일치, 기대 0건, 기대 0 나누기)는 행이 안 생기고 다음 실행이
        다시 본다.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(PENDING_JUDGMENT)
            claim_groups = _group_by_event(_claim_rows(cursor.fetchall()))
            cursor.execute(PENDING_EARNINGS_EXPECTATIONS)
            earnings_groups = _group_by_event(_claim_rows(cursor.fetchall()))

        judged: list[JudgedOutcome] = []
        with self._connection.cursor() as cursor:
            for key, rows in claim_groups.items():
                actual = resolve_actual(rows)
                if actual is None:
                    continue
                outcome = self._judge_one(cursor, key, rows, actual, dag_run_id)
                if outcome is not None:
                    judged.append(outcome)

            for key, rows in earnings_groups.items():
                stock_code, _, period_key, metric = key
                cursor.execute(EARNINGS_ACTUAL, (stock_code, period_end_for(period_key), metric))
                fact_rows = tuple(
                    EarningsFactRow(
                        id=row[0],
                        statement_scope=row[1],
                        amount_basis=row[2],
                        release_type=row[3],
                        rcept_no=row[4],
                        current_amount=row[5],
                        created_at=row[6],
                    )
                    for row in cursor.fetchall()
                )
                actual = resolve_earnings_actual(fact_rows, period_key)
                if actual is None:
                    continue
                outcome = self._judge_one(cursor, key, rows, actual, dag_run_id)
                if outcome is not None:
                    judged.append(outcome)
        return tuple(judged)

    @staticmethod
    def _judge_one(
        cursor: Cursor,
        key: tuple[str, str, str, str],
        rows: Sequence[ClaimRow],
        actual: tuple[Decimal, datetime, str],
        dag_run_id: str,
    ) -> JudgedOutcome | None:
        stock_code, event_type, period_key, metric = key
        actual_value, announced_at, actual_ref = actual
        aggregated = aggregate_expectations(rows, announced_at)
        if aggregated is None:
            # 기대가 없던 발표는 그것대로 사실이다. 억지 판정이 더 나쁘다.
            logger.info("no pre-announcement expectations for %s %s %s %s", *key)
            return None
        expected_value, expectation_count = aggregated
        classified = classify_surprise(expected_value, actual_value)
        if classified is None:
            logger.warning("cannot classify %s %s %s %s: expected 0, actual %s", *key, actual_value)
            return None
        surprise_pct, verdict = classified
        cursor.execute(
            OUTCOME_INSERT,
            (
                stock_code,
                event_type,
                period_key,
                metric,
                expected_value,
                expectation_count,
                actual_value,
                surprise_pct,
                verdict,
                announced_at,
                actual_ref,
                dag_run_id,
            ),
        )
        if cursor.fetchone() is None:
            # 동시 실행이 먼저 썼다. 첫 성공본 불변 — 이번 실행의 발송 대상이 아니다.
            return None
        return JudgedOutcome(
            stock_code=stock_code,
            event_type=event_type,
            period_key=period_key,
            metric=metric,
            expected_value=expected_value,
            expectation_count=expectation_count,
            actual_value=actual_value,
            surprise_pct=surprise_pct,
            verdict=verdict,
            announced_at=announced_at,
        )


# ---------------------------------------------------------------------------
# Slack 렌더링 — 순수 조회+포맷. 발송은 DAG가 한다.
# ---------------------------------------------------------------------------

HEADER = "📐 기대 대비 발표"


def render_text(outcomes: Sequence[JudgedOutcome]) -> str:
    """블록을 못 그리는 자리(알림, 검색)에 뜨는 대체 문구."""
    lines = [HEADER]
    lines.extend(_outcome_line(outcome) for outcome in outcomes)
    return "\n".join(lines)


def render_blocks(outcomes: Sequence[JudgedOutcome]) -> list[dict[str, Any]]:
    """판정 하나가 section 하나다. 새 판정이 있을 때만 발송하므로 0건 형태는 없다."""
    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": HEADER}},
    ]
    blocks.extend(
        {"type": "section", "text": {"type": "mrkdwn", "text": _outcome_line(outcome)}} for outcome in outcomes
    )
    return blocks


def _outcome_line(outcome: JudgedOutcome) -> str:
    return (
        f"*{outcome.stock_code}* · {EVENT_LABELS[outcome.event_type]} {outcome.period_key}"
        f" · {METRIC_LABELS[outcome.metric]}\n"
        f"발표 {format_krw(outcome.actual_value)} vs 기대 {format_krw(outcome.expected_value)}"
        f" (기대 {outcome.expectation_count}건)"
        f" → {VERDICT_LABELS[outcome.verdict]} {outcome.surprise_pct:+.1f}%"
    )
