"""추론 원장 — 저장과 조회 전부를 쥔 `ThesisStore`.

연결 하나가 객체 하나다. 생성·채점·해설·발송이 전부 같은 원장을 보므로 층마다 store를
따로 두지 않는다.

**생성자는 연결뿐이다.** 채점과 해설은 이 실행의 날짜가 아니라 *지난* 추론의 날짜를 돌며
부르기 때문에, `run_date`·`run_slot`을 생성자에 담으면 그 값이 호출마다 거짓이 된다.
"""

"""시장 추론(thesis)을 만들고, 저장하고, 채점한다.

**목적은 정확도다 — 다만 개별 추론이 아니라 판(版)의 정확도다.** 한 건의 적중은 운과
구분되지 않으므로 "어떤 정보를 근거로 어떤 결론을 냈다"를 먼저 기록으로 남기고, 채점이
쌓이면 model·prompt 판별로 비교해 다음 변경을 유지하거나 되돌린다. **이미 쓴 추론은
고치지 않는다** — 고칠 수 있으면 나쁜 판이 사후 수정으로 좋아 보인다.

## 근거는 고정 풀이 아니라 모델이 조회한다

프롬프트에는 **관측 상태만** 준다("코스피 +1.61%", "SK하이닉스 전일 -2.1%"). 관측 상태는
전부 SQL이 계산한다. 왜인지 알아내는 데 필요한 정보는 모델이 `ThesisToolbox`의 읽기 전용 툴을
호출해 스스로 가져온다 — 어떤 것을 얼마나 볼지는 모델이 정한다.

**모델이 실제로 인용한 근거만 저장한다.** 툴이 돌려준 항목에는 전부 `ref`가 붙어 있고,
답변의 `evidence_refs`는 그 레지스트리로 검증한다. 목록 밖 ref는 버린다. 이것이 모델이 근거를
지어내지 못하게 막는 유일한 장치다.

## 조사와 답변을 나눈다

`modules/llm.py`의 원칙 그대로다. 조사 단계는 툴만 바인딩하고, 답변 단계는 툴을 빼고
`response_format`을 강제한다. 한 요청에 둘을 섞지 않는다 — `llm.invoke`가 그것을 막는다.

## 기준 시각은 벽시계가 아니다

**모든 조회의 끝은 슬롯이 정한 `as_of_at`이다.** 오후에 장전 슬롯을 다시 돌려도 장중 정보로
아침 예측을 덮지 않는다. 이것은 event-time cutoff다 — 현재 DB에서 확인 가능한 범위에서
`as_of_at` 이후 감지·평가·갱신된 행을 뺀다. 과거 시점을 완전히 복원하지는 못한다
(`document`는 본문·평가를 같은 행에 덮어쓰고 버전 이력을 두지 않는다).

## 첫 성공본은 불변이다

같은 (날짜, 슬롯)에 추론 행이 이미 있으면 LLM을 다시 부르지 않는다. LLM은 재호출마다 답이
달라서 덮어쓰면 최초 판단이 사라진다. `existing_theses`가 먼저 보고, 없을 때만 Builder를 돈다.

## 채점에 LLM이 없다

수식이 SQL이 아니라 파이썬에 있는 이유는 경계값을 DB 없이 테스트하기 위해서다(테스트에서
실 DB를 쓰지 않는 프로젝트 규칙). `select_session_return.sql`이 등락률을 주고
`update_outcome.sql`은 여기서 나온 값 넷을 쓰기만 한다.

설계는 `docs/analysis/market-thesis/1-storage.md`와 `docs/analysis/market-thesis/2-agent.md`에 있다.
"""

import json
import logging
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict

from modules.db import Cursor
from modules.db import TransactionalConnection as Connection
from modules.sql import read_sql
from modules.thesis.domain import (
    HORIZON_DAYS,
    NARRATED_HORIZON_DAYS,
    PROMPT_VERSION,
    Evidence,
    LlmRunKind,
    LlmRunStatus,
    Subject,
    ThesisError,
    ThesisSubjectKind,
    ToolCallRecord,
    brier_score,
    classify_outcome,
    return_error,
)
from modules.thesis.generation import (
    Claim,
    ThesisDraft,
)
from modules.thesis.outcomes import (
    NarrativeDraft,
    NarrativeTarget,
)
from modules.thesis.state import (
    FORECAST_SLOTS,
    NARRATED_SLOTS,
    NxtObservedState,
    ObservedState,
    PastOutcome,
    PastThesis,
    RunSlot,
)
from modules.utility import atomic

logger = logging.getLogger(__name__)
PAST_THESES = read_sql("postgres", "thesis", "select_past_with_outcomes.sql")
PRECEDENT_INSERT = read_sql("postgres", "thesis_precedent", "insert.sql")


# ---------------------------------------------------------------------------
# 저장 — 첫 성공본 불변
# ---------------------------------------------------------------------------

LLM_RUN_INSERT = read_sql("postgres", "thesis_llm_run", "insert.sql")
LLM_RUN_FINISH = read_sql("postgres", "thesis_llm_run", "update_finish.sql")
TOOL_CALL_INSERT = read_sql("postgres", "thesis_tool_call", "insert.sql")
THESIS_INSERT = read_sql("postgres", "thesis", "insert.sql")
THESIS_SELECT_BY_RUN = read_sql("postgres", "thesis", "select_by_run.sql")
EVIDENCE_INSERT = read_sql("postgres", "thesis_evidence", "insert.sql")


class StoredThesis(BaseModel):
    """저장된 추론 한 행. `select_by_run.sql`의 행 계약이다."""

    model_config = ConfigDict(frozen=True)

    id: int
    run_slot: RunSlot
    run_date: date
    as_of_at: datetime
    dag_run_id: str
    subject_kind: ThesisSubjectKind
    subject_code: str
    label: str
    prob_up: Decimal
    prob_down: Decimal
    prob_flat: Decimal
    up_reasoning: str
    down_reasoning: str
    flat_reasoning: str
    tool_rounds: int
    llm_model: str
    prompt_version: str
    # 판 7부터의 방향별 조건부 크기. 그 전에 저장된 행은 둘 다 `None`이라 렌더가
    # 확률만 그리던 모양으로 떨어진다.
    up_return_pct: Decimal | None = None
    down_return_pct: Decimal | None = None


WATCHED_INSTRUMENTS = read_sql("postgres", "instrument", "select_watched.sql")

# 추론 대상 지수. `quote_symbol`이 아니라 여기 두는 이유는 이것이 "무엇을 추론할지"의
# 목록이지 "어떤 심볼을 수집할지"가 아니기 때문이다. KOSPI200은 코스피와 거의 같이 움직여
# 대상에서 뺀다 — 같은 판단을 두 번 적는 것이 된다.
INDEX_SUBJECTS: tuple[tuple[str, str], ...] = (("KOSPI", "코스피"), ("KOSDAQ", "코스닥"))


def _store_evidence(
    cursor: Cursor,
    thesis_id: int,
    refs: Iterable[str],
    registry: dict[str, Evidence],
    outcome_horizon_days: int | None = None,
    claims: Sequence[Claim] = (),
) -> None:
    """인용 순서를 `rank`로 굳혀 근거를 넣는다. 1부터 센다.

    `outcome_horizon_days`가 `None`이면 원 추론이 인용한 근거이고, 1·3·5면 그 지평의 사후
    해설이 인용한 근거다. 같은 테이블에 들어가고 그 칸이 둘을 가른다.

    `claims`는 원 추론의 인용에만 온다 — ref마다 방향과 경로다. 해설의 인용은 둘 다 NULL이다.
    """
    by_ref = {claim.ref: claim for claim in claims}
    for rank, ref in enumerate(refs, start=1):
        item = registry[ref]
        claim = by_ref.get(ref)
        cursor.execute(
            EVIDENCE_INSERT,
            (
                thesis_id,
                outcome_horizon_days,
                item.kind.value,
                item.ref,
                item.title,
                item.url,
                json.dumps(item.detail.model_dump(mode="json"), ensure_ascii=False),
                rank,
                claim.direction.value if claim else None,
                claim.mechanism if claim else None,
            ),
        )


def _stored(row: Sequence[Any]) -> StoredThesis:
    return StoredThesis(
        id=row[0],
        run_slot=row[1],
        run_date=row[2],
        as_of_at=row[3],
        dag_run_id=row[4],
        subject_kind=row[5],
        subject_code=row[6],
        label=row[7],
        prob_up=row[8],
        prob_down=row[9],
        prob_flat=row[10],
        up_reasoning=row[11],
        down_reasoning=row[12],
        flat_reasoning=row[13],
        tool_rounds=row[14],
        llm_model=row[15],
        prompt_version=row[16],
        up_return_pct=row[17],
        down_return_pct=row[18],
    )


# ---------------------------------------------------------------------------
# 다지평 채점 — LLM 없음
# ---------------------------------------------------------------------------

PENDING_GRADES = read_sql("postgres", "thesis_outcome", "select_pending_grades.sql")
INSERT_GRADE = read_sql("postgres", "thesis_outcome", "insert_grade.sql")
NTH_OPEN_DAY = read_sql("postgres", "market_session", "select_nth_open_day.sql")
STOCK_HORIZON_RETURN = read_sql("postgres", "stock_investor_trade_daily", "select_horizon_return.sql")
INDEX_HORIZON_RETURN = read_sql("postgres", "index_bar", "select_horizon_return.sql")
# 장중 슬롯은 기준가가 전일 종가가 아니라 그 슬롯이 본 봉이라 조회가 갈린다. 기존 두
# 파일에 분기를 얹지 않고 파일을 나눴다 — 잘 돌고 있는 `pre_open` 경로가 조용히 따라
# 바뀌면 안 된다(각 파일 머리말).
STOCK_INTRADAY_HORIZON_RETURN = read_sql(
    "postgres", "stock_investor_trade_daily", "select_intraday_horizon_return.sql"
)
INDEX_INTRADAY_HORIZON_RETURN = read_sql("postgres", "index_bar", "select_intraday_horizon_return.sql")


class PendingGrade(BaseModel):
    """채점을 기다리는 (추론, 지평) 하나. `select_pending_grades.sql`의 행 계약이다."""

    model_config = ConfigDict(frozen=True)

    thesis_id: int
    run_date: date
    as_of_at: datetime
    subject_kind: ThesisSubjectKind
    subject_code: str
    prob_up: Decimal
    prob_down: Decimal
    prob_flat: Decimal
    horizon_days: int
    run_slot: RunSlot
    # 장중 슬롯의 채점 기준가. 추론 행의 `input_state`에 박혀 있는, **모델이 실제로 본**
    # 가격이다. 장전 슬롯은 `None`이고 기준가를 전일 종가에서 얻는다.
    base_price: Decimal | None = None
    # 크기 채점의 입력. 실현된 방향에 대응하는 쪽만 쓰인다(`thesis.domain.return_error`).
    up_return_pct: Decimal | None = None
    down_return_pct: Decimal | None = None


# ---------------------------------------------------------------------------
# 해설 저장
# ---------------------------------------------------------------------------

PENDING_NARRATIVES = read_sql("postgres", "thesis_outcome", "select_pending_narratives.sql")
INSERT_NARRATIVE = read_sql("postgres", "thesis_outcome", "insert_narrative.sql")


# ---------------------------------------------------------------------------
# Slack 렌더링
#
# `briefing/market.py`가 자기 도메인 렌더링을 갖는 것과 같다. thesis는 정기 리포트
# 3부작과 다른 도메인이라 `briefing/` 아래 두지 않는다.
# ---------------------------------------------------------------------------

EVIDENCE_SELECT_TOP = read_sql("postgres", "thesis_evidence", "select_top_by_thesis_ids.sql")

# 근거를 DB에서 가져올 개수. 표시 개수보다 넉넉히 받아 결론 방향으로 거른 뒤 자른다.
# 인용은 실행당 20건이 상한이라(`MAX_TOOL_RESULTS`) 이 값이면 사실상 전부다.
EVIDENCE_FETCH_LIMIT = 12


class StoredEvidence(BaseModel):
    """저장된 근거 한 행. Slack 근거 줄이 쓴다.

    `direction`·`mechanism`은 해설이 인용한 근거에서는 비어 있다(`store_narratives`가
    claims 없이 저장한다). 원 추론의 근거여도 모델이 `claims`에 안 담고 `evidence_refs`로만
    올린 것은 마찬가지로 비어 있다 — 그래서 옵셔널이다.
    """

    model_config = ConfigDict(frozen=True)

    thesis_id: int
    evidence_title: str
    evidence_url: str | None = None
    rank: int
    direction: str | None = None
    mechanism: str | None = None


class ThesisStore:
    """추론 원장을 읽고 쓴다. **연결이 상태다.**

    전에는 조회·저장 열두 함수가 각각 `connection`을 첫 인자로 받았고, DAG과 흐름 모듈이
    그 값을 태스크마다 다시 실어 날랐다. 저장소에서 같은 인자가 가장 많이 반복되던 자리다.

    **`run_date`·`run_slot`·`dag_run_id`는 생성자가 아니라 메서드 인자다.** 채점과 해설은
    이 실행의 날짜가 아니라 *지난* 추론의 날짜를 돌며 부른다 — 생성자에 담으면 그 값이
    호출마다 거짓이 된다. 연결만이 객체 하나가 사는 동안 안 변한다.

    렌더링(`render_blocks`·`render_text`)과 계산(`classify_outcome`·`brier_score`)은 여기
    없다. 감쌀 상태가 없어 모듈 함수로 남는다.
    """

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    # --- LLM 실행 원장 -------------------------------------------------------

    def start_llm_run(
        self,
        *,
        kind: LlmRunKind,
        run_date: date,
        run_slot: RunSlot,
        as_of_at: datetime,
        dag_run_id: str,
        try_number: int,
        llm_model: str,
        prompt_version: str,
        horizon_days: int | None = None,
    ) -> int:
        """대화 하나를 `running`으로 열고 그 id를 준다. **그래프를 부르기 전에 커밋한다.**

        대화가 죽어도 "시작했다"는 사실이 남아야 한다. 실패한 대화가 원장에 없으면 패턴
        분석이 성공한 실행만 보게 되고, 그게 이 단계 전의 상태다.

        판단 저장과 **다른 트랜잭션이다.** 원장이 못 써졌다고 추론을 버리면 안 되고,
        추론 저장이 실패해도 "무엇을 봤나"는 남아야 한다.
        """
        with atomic(self._connection) as transaction, transaction.cursor() as cursor:
            cursor.execute(
                LLM_RUN_INSERT,
                (
                    kind.value,
                    run_date,
                    run_slot.value,
                    horizon_days,
                    as_of_at,
                    dag_run_id,
                    try_number,
                    llm_model,
                    prompt_version,
                    datetime.now(UTC),
                ),
            )
            row = cursor.fetchone()
        if row is None:
            raise ThesisError("failed to open an llm run ledger row")
        return int(row[0])

    def finish_llm_run(
        self,
        llm_run_id: int,
        *,
        status: LlmRunStatus,
        records: Sequence[ToolCallRecord],
        tool_rounds: int,
        investigation_truncated: bool = False,
        subjects_requested: int | None = None,
        subjects_answered: int | None = None,
        error: str | None = None,
    ) -> None:
        """대화를 닫고 그 안의 툴 호출을 한 트랜잭션에 쓴다.

        **총량 둘은 상한을 재는 카운터와 다른 수다.** `tool_calls`는 기록된 행 수라 모르는
        툴과 인자 검증 실패도 세지만 툴박스의 예산 카운터는 함수에 진입한 것만 센다.
        `tool_result_chars`는 모델에게 실제로 돌아간 것만(`delivered`) 센다 — 예산 카운터는
        버려진 결과도 센다. 둘 다 `MAX_TOOL_*`와 직접 비교하지 않는다.

        `investigation_truncated`의 기본이 `False`인 것은 **해설 경로에는 왕복 상한이 없기
        때문이다.** 그쪽은 이 인자를 주지 않는다. 추론 생성만 실제 값을 넘긴다.

        `subjects_*` 둘의 기본이 `None`인 것도 같은 이유다. 해설은 대상 개념이 달라
        **0이 아니라 NULL이다** — 0으로 넣으면 "전부 실패한 생성"과 같아 보인다.
        """
        delivered_chars = sum(record.result_chars for record in records if record.delivered)
        with atomic(self._connection) as transaction, transaction.cursor() as cursor:
            cursor.execute(
                LLM_RUN_FINISH,
                (
                    status.value,
                    datetime.now(UTC),
                    error,
                    tool_rounds,
                    len(records),
                    delivered_chars,
                    investigation_truncated,
                    subjects_requested,
                    subjects_answered,
                    llm_run_id,
                ),
            )
            for record in records:
                cursor.execute(
                    TOOL_CALL_INSERT,
                    (
                        llm_run_id,
                        record.seq,
                        record.round_no,
                        record.tool_call_id,
                        record.tool_name,
                        json.dumps(record.arguments, ensure_ascii=False, default=str),
                        None
                        if record.validated_arguments is None
                        else json.dumps(record.validated_arguments, ensure_ascii=False, default=str),
                        record.requested_at,
                        record.duration_ms,
                        record.result_chars,
                        record.result,
                        record.delivered,
                        None if record.error_kind is None else record.error_kind.value,
                        record.error,
                    ),
                )

    def past_theses(self, *, as_of_at: datetime, subject_code: str, n: int) -> list[PastThesis]:
        """이 대상의 지난 추론과 지평별 결과. **슬롯마다** 최근 것부터 `n`건이다.

        피드백 루프는 이 조회 하나다. 예측 슬롯 다섯과 장후 리뷰(`post_close`)를 함께
        돌려준다 — 리뷰에 붙는 사후 해설이 "그 인과 주장이 이후 보도로 지지됐나"를 담고 있어
        다음 예측이 볼 값어치가 크다.

        **`n`은 슬롯마다다.** 총량으로 자르면 장후가 섞여 들어온 만큼 장전 예측 이력이 짧아진다.
        뒤집어 말하면 총 행 수가 슬롯 수배라, 장중 넷이 붙으면서 `PREFETCHED_PAST_THESES`를
        내렸다.

        **창의 끝은 `as_of_at`이다.** 없으면 장전 슬롯을 오후에 재실행할 때 그날 저녁의 채점이
        아침 예측에 섞인다. SQL이 술어 셋을 건다.

        `n <= 0`이면 조회하지 않고 빈 목록이다 — `PREFETCHED_PAST_THESES = 0`이 끄는 스위치다.
        """
        if n <= 0:
            return []
        with self._connection.cursor() as cursor:
            cursor.execute(PAST_THESES, (as_of_at, list(NARRATED_SLOTS), subject_code, n))
            rows = cursor.fetchall()
        return [
            PastThesis(
                id=row[0],
                run_slot=row[1],
                run_date=row[2],
                prob_up=float(row[3]),
                prob_down=float(row[4]),
                prob_flat=float(row[5]),
                up_reasoning=row[6],
                down_reasoning=row[7],
                flat_reasoning=row[8],
                # SQL이 jsonb_agg로 만든 목록. 모양은 `PastOutcome`이 검증한다.
                outcomes=tuple(PastOutcome.model_validate(outcome) for outcome in row[9]),
            )
            for row in rows
        ]

    def subjects(self) -> tuple[Subject, ...]:
        """이번 실행의 추론 대상. 지수는 코드가, 종목은 `instrument.is_watched`가 정한다.

        종목을 마스터에서 읽는 이유는 추적 종목이 늘 때 이 모듈을 고치지 않기 위해서다.
        지수는 마스터에 없어(그쪽은 `quote_symbol`이다) 코드에 둔다.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(WATCHED_INSTRUMENTS)
            watched = cursor.fetchall()
        return (
            *(Subject(kind=ThesisSubjectKind.INDEX, code=code, label=label) for code, label in INDEX_SUBJECTS),
            *(Subject(kind=ThesisSubjectKind.STOCK, code=row[0], label=row[1]) for row in watched),
        )

    def existing_theses(self, *, run_date: date, run_slot: RunSlot) -> tuple[StoredThesis, ...]:
        """이 (날짜, 슬롯)에 이미 저장된 추론.

        **부르는 쪽은 LLM을 부르기 전에 이것을 먼저 본다.** 비어 있지 않으면 모델을 부르지 않는다
        (첫 성공본 불변). 재실행은 기존 행을 읽어 다음 태스크로 넘길 뿐이다.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(THESIS_SELECT_BY_RUN, (run_date, run_slot.value))
            rows = cursor.fetchall()
        return tuple(_stored(row) for row in rows)

    def store_theses(
        self,
        *,
        run_date: date,
        run_slot: RunSlot,
        as_of_at: datetime,
        dag_run_id: str,
        drafts: Sequence[ThesisDraft],
        registry: dict[str, Evidence],
        observed_state: ObservedState | NxtObservedState,
        llm_model: str,
        tool_rounds: int,
        precedents: Mapping[str, Sequence[int]],
        llm_run_id: int | None = None,
    ) -> tuple[StoredThesis, ...]:
        """추론과 근거, 그리고 본 과거 추론을 한 트랜잭션에 쓴다.

        **추론은 `INSERT ... ON CONFLICT DO NOTHING`이다.** 같은 (날짜, 슬롯, subject)에 행이 이미
        있으면 아무 것도 바꾸지 않는다. `RETURNING`이 0행이면 삽입 직전에 다른 실행이 먼저 넣은
        것이므로, 그 경우에도 실패로 보지 않고 저장된 행을 읽어 돌려준다.

        thesis와 evidence를 한 트랜잭션에 쓴다 — 추론만 들어가고 근거가 빠진 상태를 남기지 않는다.
        `precedents`는 subject 코드별로 프롬프트에 실린 과거 thesis ID 목록이고 `thesis_precedent`
        엣지가 된다. 같은 트랜잭션이다 — "무엇을 보고 냈나"도 추론과 함께 들어가거나 함께 빠진다.
        """
        with atomic(self._connection) as transaction, transaction.cursor() as cursor:
            for draft in drafts:
                cursor.execute(
                    THESIS_INSERT,
                    (
                        run_slot.value,
                        run_date,
                        as_of_at,
                        dag_run_id,
                        draft.subject.kind.value,
                        draft.subject.code,
                        draft.subject.label,
                        draft.prob_up,
                        draft.prob_down,
                        draft.prob_flat,
                        draft.up_return_pct,
                        draft.down_return_pct,
                        draft.up_reasoning,
                        draft.down_reasoning,
                        draft.flat_reasoning,
                        json.dumps(observed_state.model_dump(mode="json"), ensure_ascii=False),
                        tool_rounds,
                        llm_model,
                        PROMPT_VERSION,
                        llm_run_id,
                    ),
                )
                returned = cursor.fetchone()
                if returned is None:
                    logger.info("thesis for %s %s %s already existed", run_date, run_slot.value, draft.subject.code)
                    continue
                _store_evidence(cursor, returned[0], draft.evidence_refs, registry, claims=draft.claims)
                for precedent_id in precedents.get(draft.subject.code, ()):
                    cursor.execute(PRECEDENT_INSERT, (returned[0], precedent_id))

        return self.existing_theses(run_date=run_date, run_slot=run_slot)

    def pending_grades(self, horizons: Sequence[int] = HORIZON_DAYS) -> tuple[PendingGrade, ...]:
        """아직 채점하지 않은 (추론, 지평) 전부. **예측 슬롯만이다.**

        슬롯 목록도 지평 목록과 같은 이유로 파라미터다 — 상수를 SQL과 파이썬 두 곳에 두면
        한쪽만 고쳐지는 날이 온다. 원본은 `thesis.state.FORECAST_SLOTS`다.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(PENDING_GRADES, (list(horizons), list(FORECAST_SLOTS)))
            rows = cursor.fetchall()
        return tuple(
            PendingGrade(
                thesis_id=row[0],
                run_date=row[1],
                as_of_at=row[2],
                subject_kind=row[3],
                subject_code=row[4],
                prob_up=row[5],
                prob_down=row[6],
                prob_flat=row[7],
                horizon_days=row[8],
                run_slot=RunSlot(row[9]),
                # SQL이 jsonb에서 뽑은 문자열이다. 없으면(장전 슬롯) NULL이 온다.
                base_price=None if row[10] is None else Decimal(row[10]),
                up_return_pct=row[11],
                down_return_pct=row[12],
            )
            for row in rows
        )

    def nth_open_day(self, base_date: date, horizon_days: int) -> date | None:
        """`base_date`부터 세어 `horizon_days`번째 KRX 개장일. 달력이 안 채워졌으면 `None`.

        0이면 `base_date` 자신(개장일일 때)이다. **날짜를 우리가 세지 않는다** — 휴장일에서
        어긋난다. `None`이면 부르는 쪽은 그 조합을 미채점으로 남기고 다음 실행이 다시 집는다.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(NTH_OPEN_DAY, (base_date, horizon_days))
            row = cursor.fetchone()
        return row[0] if row else None

    def horizon_returns(
        self,
        *,
        subject_kind: ThesisSubjectKind,
        run_date: date,
        target_date: date,
        codes: Sequence[str],
        base_bar_at: datetime | None = None,
        target_bar_at: datetime | None = None,
    ) -> dict[str, Decimal]:
        """대상별 누적 등락률. 종가·봉이 없으면 그 대상은 결과에 없다.

        기준가는 지평이 달라도 같다 — 예측일 전 영업일 종가다. 지수는 봉 시각 둘을 받는다
        (KST 경계 계산은 파이썬이 한다).
        """
        if not codes:
            return {}
        if subject_kind is ThesisSubjectKind.STOCK:
            statement, parameters = STOCK_HORIZON_RETURN, (run_date, target_date, list(codes))
        else:
            if base_bar_at is None or target_bar_at is None:
                raise ThesisError("index horizon returns need both bar timestamps")
            statement, parameters = INDEX_HORIZON_RETURN, (base_bar_at, target_bar_at, list(codes))

        with self._connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            rows = cursor.fetchall()
        return {row[0]: row[4] for row in rows if row[4] is not None}

    def intraday_horizon_returns(
        self,
        *,
        subject_kind: ThesisSubjectKind,
        target_date: date,
        target_bar_at: datetime | None = None,
        base_prices: Mapping[str, Decimal],
    ) -> dict[str, Decimal]:
        """장중 슬롯의 대상별 누적 등락률. **기준가를 우리가 준다.**

        `horizon_returns`와 갈리는 축은 분모 하나다. 저쪽은 예측일 전 영업일 종가를 SQL이
        찾아 쓰고, 이쪽은 그 슬롯이 실제로 본 봉의 종가를 부르는 쪽이 준다. 봉에서 다시
        뽑지 않는 이유는 그 사이 수집 재실행이 없던 봉을 채워 "직전 봉"이 달라질 수 있기
        때문이다 — 모델이 본 값과 채점 분모가 어긋나면 안 된다.

        지수는 목표 봉 시각(KST 경계 계산은 파이썬이 한다), 종목은 목표 거래일을 쓴다.
        값이 없으면 그 대상은 결과에 없고 부르는 쪽이 미채점으로 남긴다.
        """
        if not base_prices:
            return {}
        codes = list(base_prices)
        prices = [base_prices[code] for code in codes]
        if subject_kind is ThesisSubjectKind.STOCK:
            statement, parameters = STOCK_INTRADAY_HORIZON_RETURN, (codes, prices, target_date)
        else:
            if target_bar_at is None:
                raise ThesisError("index intraday horizon returns need the target bar timestamp")
            statement, parameters = INDEX_INTRADAY_HORIZON_RETURN, (codes, prices, target_bar_at)

        with self._connection.cursor() as cursor:
            cursor.execute(statement, parameters)
            rows = cursor.fetchall()
        return {row[0]: row[3] for row in rows if row[3] is not None}

    def store_grade(
        self,
        *,
        pending: PendingGrade,
        as_of_at: datetime,
        dag_run_id: str,
        return_pct: Decimal,
        evaluated_at: datetime,
    ) -> None:
        """한 (추론, 지평)의 채점을 쓴다. 이미 매긴 점수는 덮지 않는다(SQL의 WHERE가 막는다)."""
        outcome = classify_outcome(return_pct, pending.horizon_days)
        score = brier_score(
            prob_up=pending.prob_up,
            prob_down=pending.prob_down,
            prob_flat=pending.prob_flat,
            outcome=outcome,
        )
        # 크기 채점은 방향 채점과 **같은 행**이고 지평 0에서만 잰다 — 크기의 창이 확률과
        # 같은 창이라 5영업일 누적에 대조하면 항상 과소로 나온다. 잴 수 없으면 둘 다 NULL.
        sizing = (
            return_error(
                actual_return_pct=return_pct,
                outcome=outcome,
                up_return_pct=pending.up_return_pct,
                down_return_pct=pending.down_return_pct,
            )
            if pending.horizon_days == 0
            else None
        )
        predicted_return_pct, return_error_pct = sizing if sizing else (None, None)
        with self._connection.cursor() as cursor:
            cursor.execute(
                INSERT_GRADE,
                (
                    pending.thesis_id,
                    pending.horizon_days,
                    as_of_at,
                    dag_run_id,
                    evaluated_at,
                    return_pct,
                    outcome.value,
                    score.quantize(Decimal("0.00001")),
                    predicted_return_pct,
                    return_error_pct,
                ),
            )

    def pending_narratives(
        self,
        *,
        run_date: date,
        horizon_days: int,
    ) -> tuple[NarrativeTarget, ...]:
        """그 지평에서 아직 해설이 없는 대상. **`NARRATED_SLOTS` 전부가 온다.**

        채점 값이 있으면 함께 담는다. 프롬프트에 실을지는 `FollowupNarrator`의
        `include_outcome`이 정한다 — 이 함수는 있는 대로 준다.

        `cited_titles`는 여기서 채우지 않는다. 원 추론이 인용한 근거 제목은
        `thesis_evidence`에 있고, 부르는 쪽이 필요하면 붙인다.
        """
        if horizon_days not in NARRATED_HORIZON_DAYS:
            raise ThesisError(f"horizon {horizon_days} does not take a narrative; known: {NARRATED_HORIZON_DAYS}")
        with self._connection.cursor() as cursor:
            cursor.execute(PENDING_NARRATIVES, (horizon_days, run_date, list(NARRATED_SLOTS)))
            rows = cursor.fetchall()
        return tuple(
            NarrativeTarget(
                thesis_id=row[0],
                run_slot=RunSlot(row[2]),
                subject=Subject(kind=row[3], code=row[4], label=row[5]),
                prob_up=row[6],
                prob_down=row[7],
                prob_flat=row[8],
                up_reasoning=row[9],
                down_reasoning=row[10],
                flat_reasoning=row[11],
                actual_return_pct=row[12],
                actual_outcome=row[13],
                brier_score=row[14],
                predicted_return_pct=row[15],
                return_error_pct=row[16],
            )
            for row in rows
        )

    def store_narratives(
        self,
        *,
        horizon_days: int,
        as_of_at: datetime,
        dag_run_id: str,
        drafts: Sequence[NarrativeDraft],
        registry: dict[str, Evidence],
        llm_model: str,
        prompt_revision: str,
        narration_run_id: int | None = None,
    ) -> int:
        """해설과 그 근거를 한 트랜잭션에 쓴다. 쓴 건수를 돌려준다.

        **해설 갱신과 근거 INSERT가 한 트랜잭션이다.** 해설만 들어가고 근거가 빠진 상태를
        남기지 않는다 — 근거 없는 판정은 되짚을 수 없다.

        이미 해설이 있는 행은 SQL의 `WHERE narrative IS NULL`이 막는다. 그때 근거를 다시
        넣지 않도록 `RETURNING`으로 실제 갱신 여부를 확인한다.
        """
        if horizon_days not in NARRATED_HORIZON_DAYS:
            raise ThesisError(f"horizon {horizon_days} does not take a narrative; known: {NARRATED_HORIZON_DAYS}")

        stored = 0
        with atomic(self._connection) as transaction, transaction.cursor() as cursor:
            for draft in drafts:
                cursor.execute(
                    INSERT_NARRATIVE,
                    (
                        draft.thesis_id,
                        horizon_days,
                        as_of_at,
                        dag_run_id,
                        draft.narrative,
                        draft.verdict.value,
                        datetime.now(UTC),
                        llm_model,
                        prompt_revision,
                        narration_run_id,
                    ),
                )
                if cursor.rowcount == 0:
                    # 다른 실행이 먼저 썼다. 근거를 덧붙이면 그 해설과 어긋난 인용이 남는다.
                    logger.info("thesis %s already had a T+%s narrative", draft.thesis_id, horizon_days)
                    continue
                _store_evidence(cursor, draft.thesis_id, draft.evidence_refs, registry, horizon_days)
                stored += 1
        return stored

    def top_evidence(
        self,
        thesis_ids: Sequence[int],
        *,
        outcome_horizon_days: int | None = None,
        limit: int = EVIDENCE_FETCH_LIMIT,
    ) -> dict[int, tuple[StoredEvidence, ...]]:
        """추론별 상위 근거. `outcome_horizon_days`가 `None`이면 원 추론이 인용한 것이다.

        기본 상한이 표시 개수(`SLACK_EVIDENCE_LIMIT`)가 아니라 `EVIDENCE_FETCH_LIMIT`인 것은
        부르는 쪽이 채택 방향으로 한 번 더 거르기 때문이다.
        """
        if not thesis_ids:
            return {}
        with self._connection.cursor() as cursor:
            cursor.execute(EVIDENCE_SELECT_TOP, (list(thesis_ids), outcome_horizon_days, limit))
            rows = cursor.fetchall()
        grouped: dict[int, list[StoredEvidence]] = {}
        for row in rows:
            grouped.setdefault(row[0], []).append(
                StoredEvidence(
                    thesis_id=row[0],
                    evidence_title=row[4],
                    evidence_url=row[5],
                    rank=row[6],
                    direction=row[7],
                    mechanism=row[8],
                )
            )
        return {thesis_id: tuple(items) for thesis_id, items in grouped.items()}
