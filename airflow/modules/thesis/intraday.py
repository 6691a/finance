"""장중 전망(`intraday_*`·`pre_close`)에만 쓰는 것. `market_thesis_intraday` DAG가 부른다.

여기 있는 것은 전부 "장이 열려 있는 동안"이라서 그런 것이다. 장전·장후와 공유하지 않는다 —
공유하면 다시 `if slot ==`이 생긴다. 슬롯을 모르는 것은 `thesis/common.py`에 있다.

## 무엇이 다른가

- **기준가가 전일 종가가 아니라 지금 가격이다.** 10:35 슬롯은 "10:35 가격에서 마감까지"를
  맞힌다. 이미 오른 만큼은 예측에 안 들어간다. 그래서 채점 조회도 갈린다
  (`thesis.store.intraday_horizon_returns`).
- **확정 종가를 못 본다.** `stock_investor_trade_daily`는 18:10에 들어오고 KIS가 15:40 전
  당일 조회를 거절한다. 관측 상태를 `index_bar`·`stock_bar`의 봉에서 만드는 이유다.
- **오늘 앞 슬롯을 되짚는다.** 아침 예측이 지금 맞고 있는지가 다음 판단의 재료다.
  저장하지 않고 프롬프트에만 싣는다 — 자세한 이유는 `thesis.state.SameDayThesis`.

## 왜 슬롯 넷이 DAG 하나인가

저장소 규칙은 "슬롯·모드로 갈리는 DAG는 나눈다"지만, 그 규칙이 막는 것은 **앞단 데이터와
실패 성격이 다른 것을 시각으로 뭉뚱그리는 일**이다. 장중 넷은 같은 봉과 같은 문서 평가를
같은 이유로 기다린다 — `slack_kr_market_briefing`이 하나로 남아 있는 것과 같은 경우다.

대신 그때 문제가 됐던 "수동 트리거가 벽시계로 떨어져 조용히 다른 슬롯을 돈다"는
`resolve_slot`이 막는다. Param도 `logical_date`도 없으면 **실패시킨다.**
"""

import logging
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from airflow.sdk import Param, get_current_context
from airflow.sdk.exceptions import AirflowFailException

from modules.db import Connection
from modules.sql import read_sql
from modules.thesis import common
from modules.thesis.domain import SLOT_LABELS, ForecastBaseline, LlmRunKind, ThesisSubjectKind
from modules.thesis.state import (
    INTRADAY_SLOT_TIMES,
    IntradayObservation,
    ObservedState,
    RunSlot,
    SameDayThesis,
    ThesisRunResult,
)
from modules.utility import KST_TIMEZONE

logger = logging.getLogger(__name__)

RUN_SLOT_PARAM = "run_slot"

# readiness guard가 보는 문서 평가 지연 허용치. **장전(20분)과 다르다.**
# `document_assessment_hourly`가 매시 :25에 돌아 평가 시각이 :25~:28에 몰린다. :35 슬롯은
# 그 차이가 10분뿐이지만 `pre_close`(15:00 = UTC :00)는 직전 :25까지 35분이라, 20분으로는
# 어떤 실행도 통과하지 못했다(2026-08-26 관측 — max(assessed_at) 05:26 < 05:40).
# 40분은 네 슬롯 모두에서 "직전 :25 실행은 통과, 한 시간 전 실행은 탈락"이 되는 값이다.
ASSESSMENT_LAG = timedelta(minutes=40)

# 기준 시각에서 이만큼 안의 봉이어야 "지금 가격"으로 인정한다. 정상이면 **1분**이다
# (2026-08-25 실측 — 네 슬롯 모두 직전 1분봉을 봤다). 15분은 지수 수집(`kis_quote_intraday`,
# `*/5`)이 두어 주기 밀린 것까지 봐 주는 값이고, 넘으면 `ThesisNotReady`로 Airflow 재시도에
# 맡긴다. 이 값이 크면 오래된 가격을 "지금"으로 읽는다.
BAR_STALENESS = timedelta(minutes=15)

INDEX_LATEST_BEFORE = read_sql("postgres", "index_bar", "select_latest_before.sql")
STOCK_LATEST_BEFORE = read_sql("postgres", "stock_bar", "select_latest_before.sql")
SAME_DAY_THESES = read_sql("postgres", "thesis", "select_same_day.sql")

# `_bars`가 돌려주는 대상 하나의 봉: (bar_at, close, previous_close).
Bar = tuple[datetime, Decimal, Decimal]


def as_of(run_date: date, run_slot: RunSlot) -> datetime:
    """조회 창의 끝(UTC). 모든 툴이 이 시각까지만 본다.

    **벽시계를 쓰지 않는다** — 저녁에 이 DAG를 clear해 다시 돌려도 마감 뒤 정보로 오전
    판단을 덮지 않는다. 슬롯이 시각을 정하고 그 표는 `thesis.state`에 있다.
    """
    slot_time = INTRADAY_SLOT_TIMES.get(run_slot)
    if slot_time is None:
        raise AirflowFailException(f"{run_slot.value} is not an intraday slot")
    return datetime.combine(run_date, slot_time, tzinfo=KST_TIMEZONE).astimezone(UTC)


def run_slot_param() -> dict[str, Param]:
    """장중 DAG 전용 Param. 수동 실행이 슬롯을 **명시하는** 정식 경로다."""
    return {
        RUN_SLOT_PARAM: Param(
            None,
            type=["null", "string"],
            enum=[None, *(slot.value for slot in INTRADAY_SLOT_TIMES)],
            title="장중 슬롯",
            description=(
                "비우면 스케줄된 시각으로 정한다. 수동 실행은 반드시 고른다 — "
                "스케줄 시각이 아닌 채로 비워 두면 태스크가 실패한다."
            ),
        ),
    }


def resolve_slot(context: Any) -> RunSlot:
    """이 실행의 슬롯. **벽시계로 떨어지지 않는다.**

    Param이 먼저다. 없으면 스케줄된 `logical_date`의 KST 시각을 슬롯 표에서 역조회한다.
    둘 다 아니면 실패시킨다 — 2026-08-21에 `market_thesis_analysis`를 가른 이유가 그것이다.
    조용히 다른 슬롯을 도는 것보다 안 도는 편이 낫다.
    """
    given = (context.get("params") or {}).get(RUN_SLOT_PARAM)
    if given:
        try:
            slot = RunSlot(str(given).strip())
        except ValueError as error:
            raise AirflowFailException(f"{RUN_SLOT_PARAM} {given!r} is not a known slot") from error
        if slot not in INTRADAY_SLOT_TIMES:
            raise AirflowFailException(f"{RUN_SLOT_PARAM} {slot.value} is not an intraday slot")
        return slot

    logical = context.get("logical_date")
    if logical is None:
        raise AirflowFailException(
            f"a manual run must choose {RUN_SLOT_PARAM}; the wall clock does not decide the slot"
        )
    local = logical.astimezone(KST_TIMEZONE).time().replace(second=0, microsecond=0)
    for slot, slot_time in INTRADAY_SLOT_TIMES.items():
        if local == slot_time:
            return slot
    raise AirflowFailException(f"{local:%H:%M} KST is not an intraday slot; pass {RUN_SLOT_PARAM}")


class IntradayForecast:
    """장중 전망 한 번. 연결·세션 날짜·슬롯을 들고 돈다.

    `thesis.forecast.PreOpenForecast`와 같은 모양이고 슬롯을 생성자로 받는 것만 다르다.
    슬롯은 **값으로 흐를 뿐** 이 클래스 안에서 분기를 만들지 않는다 — 시각 하나를 고르는
    표 조회(`as_of`)가 전부다.
    """

    def __init__(self, connection: Connection, *, run_date: date, run_slot: RunSlot) -> None:
        self._slot = run_slot
        self._run = common.ThesisRun(connection, run_date=run_date, as_of_at=as_of(run_date, run_slot))

    @property
    def connection(self) -> Connection:
        return self._run.connection

    @property
    def run_slot(self) -> RunSlot:
        return self._slot

    def macro_window_start(self) -> datetime:
        """매크로 창의 시작 = 당일 09:00. "오늘 장중에 무엇이 움직였나"가 이 창이다.

        장후 리뷰와 같은 창이라 계산이 `common`에 있다.
        """
        return common.open_at(self._run.run_date)

    # -- 봉 조회 -------------------------------------------------------------

    def _bars(self, targets: Any, before: datetime) -> dict[str, Bar]:
        """대상별 `before` 직전 봉 하나. 지수와 종목 둘 다 1분봉이다.

        고정 오프셋(`as_of_at - 1분`)으로 집지 않는다. 수집 주기가 달라서다 — 지수는
        `kis_quote_intraday`가 5분마다 REST로 채우고 종목은 WebSocket이 실시간으로 쌓는다.

        하한은 당일 개장이다. 없으면 수집이 죽은 날에도 어제 마감 봉이 "지금 가격"으로
        실린다(각 SQL 머리말).
        """
        index_codes = [s.code for s in targets if s.kind is ThesisSubjectKind.INDEX]
        stock_codes = [s.code for s in targets if s.kind is ThesisSubjectKind.STOCK]
        floor = self.macro_window_start()
        bars: dict[str, Bar] = {}
        with self.connection.cursor() as cursor:
            for statement, codes in ((INDEX_LATEST_BEFORE, index_codes), (STOCK_LATEST_BEFORE, stock_codes)):
                if not codes:
                    continue
                cursor.execute(statement, (codes, before, floor))
                for code, bar_at, close, previous_close in cursor.fetchall():
                    bars[code] = (bar_at, close, previous_close)
        return bars

    # -- readiness guard -----------------------------------------------------

    def check_ready(self, targets: Any) -> dict[str, Bar]:
        """봉과 문서 평가가 따라왔는지 본다. 아니면 `ThesisNotReady`로 재시도에 맡긴다.

        **확정 종가는 보지 않는다** — 18:10 전에는 그 행이 존재할 수 없다.

        조회한 봉을 그대로 돌려준다. 관측 상태가 같은 값을 다시 읽으면 그 사이에 들어온
        봉 때문에 guard가 본 것과 프롬프트에 실리는 것이 달라진다.
        """
        as_of_at = self._run.as_of_at
        bars = self._bars(targets, as_of_at)

        missing = sorted(s.code for s in targets if s.code not in bars)
        if missing:
            # 0건은 지연이 아니라 수집이 멈춘 것이다. 메시지를 나눠 로그에서 갈린다.
            raise common.ThesisNotReady(f"no intraday bars today for {', '.join(missing)}")
        stale = sorted(code for code, (bar_at, _, _) in bars.items() if as_of_at - bar_at > BAR_STALENESS)
        if stale:
            raise common.ThesisNotReady(
                f"intraday bars for {', '.join(stale)} are older than {BAR_STALENESS} at {as_of_at}"
            )

        self._check_documents()
        return bars

    def _check_documents(self) -> None:
        """문서 평가가 기준 시각을 따라왔는지. 장전 guard와 글자 그대로 같은 판정이다."""
        as_of_at = self._run.as_of_at
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT max(assessed_at) FROM document")
            latest = cursor.fetchone()[0]
            if latest is not None and latest >= as_of_at - ASSESSMENT_LAG:
                return
            # 평가할 것이 없었던 시간일 수 있다. 다만 **수집이 통째로 멈춘 것과 가려야 한다** —
            # "직전 1시간 0건"만 보면 며칠째 죽어 있어도 매번 통과한다.
            cursor.execute(
                "SELECT count(*) FILTER (WHERE detected_at >= %s), "
                "count(*) FILTER (WHERE detected_at >= %s) FROM document",
                (as_of_at - timedelta(hours=1), as_of_at - timedelta(hours=24)),
            )
            last_hour, last_day = cursor.fetchone()
        if last_hour == 0 and last_day > 0:
            logger.info("no documents arrived in the last hour; nothing was waiting for assessment")
            return
        raise common.ThesisNotReady(f"document assessment has not caught up to {as_of_at}")

    # -- 관측 상태와 되짚기 --------------------------------------------------

    def observed_state(self, targets: Any, bars: dict[str, Bar]) -> ObservedState:
        """장중 관측 상태. **`session`을 채우지 않는다** — 오늘은 아직 마감이 없다.

        `index`·`stock`(확정 마감값)도 비운다. 채우면 모델이 오늘 종가로 읽는다.
        기술적 관측은 `as_of_at`만 보므로 `ThesisRun`의 것을 그대로 쓴다.
        """
        intraday: dict[str, IntradayObservation] = {}
        for code, (bar_at, close, previous_close) in bars.items():
            if not previous_close:
                # 분모가 0이면 등락을 만들 수 없다. **로그는 남긴다** — 조용히 빠지면
                # 모델이 그 대상을 "데이터 없음"으로 읽는 이유를 아무도 되짚지 못한다.
                logger.warning("%s has no usable previous close at %s; left out of the state", code, bar_at)
                continue
            intraday[code] = IntradayObservation(
                price=float(close),
                return_pct=round(float((close - previous_close) / previous_close) * 100, 2),
                bar_at=bar_at,
            )
        codes = [target.code for target in targets]
        return ObservedState(
            intraday=intraday,
            technical=self._run.technical_state(codes),
            flat_base_rate=self._run.flat_base_rate(codes),
        )

    def same_day(self, targets: Any, bars: dict[str, Bar]) -> dict[str, list[SameDayThesis]]:
        """오늘 앞 슬롯의 추론과 그 뒤 실현 등락. 대상 코드별 목록이다.

        기준가는 **그 슬롯이 채점될 때 쓰일 값과 같다** — `pre_open`은 전일 종가이고
        장중은 그 슬롯 `as_of_at` 직전 봉의 종가다. 여기서 다른 기준을 쓰면 프롬프트가
        보여 준 성적과 밤에 매겨질 점수가 어긋난다.

        앞 슬롯의 봉을 못 찾으면 그 행은 빠진다. 되짚기는 재료이지 필수가 아니라, 없다고
        추론을 멈추지 않는다.
        """
        rows = self._same_day_rows(targets)
        if not rows:
            return {}

        # 앞 슬롯 기준가는 그 슬롯 시각 직전 봉이다. 서로 다른 시각이 많아야 셋이라
        # 시각마다 한 번씩 묻는다.
        earlier = sorted(
            {row[1] for code_rows in rows.values() for row in code_rows if row[0] is not RunSlot.PRE_OPEN}
        )
        history = {moment: self._bars(targets, moment) for moment in earlier}

        same_day: dict[str, list[SameDayThesis]] = {}
        for code, code_rows in rows.items():
            current = bars.get(code)
            if current is None:
                continue
            _, current_price, previous_close = current
            for slot, slot_at, prob_up, prob_down, prob_flat, up, down, flat in code_rows:
                base = previous_close if slot is RunSlot.PRE_OPEN else _bar_close(history.get(slot_at), code)
                if not base:
                    logger.info("no base price for the %s thesis on %s; left out of the lookback", slot.value, code)
                    continue
                same_day.setdefault(code, []).append(
                    SameDayThesis(
                        run_slot=slot,
                        as_of_at=slot_at,
                        prob_up=float(prob_up),
                        prob_down=float(prob_down),
                        prob_flat=float(prob_flat),
                        up_reasoning=up,
                        down_reasoning=down,
                        flat_reasoning=flat,
                        base_price=float(base),
                        current_price=float(current_price),
                        return_pct=round(float((current_price - base) / base) * 100, 2),
                    )
                )
        return same_day

    def _same_day_rows(self, targets: Any) -> dict[str, list[tuple[Any, ...]]]:
        """대상별 오늘 앞 슬롯 행. SQL 결과를 슬롯 enum까지만 바꿔 준다."""
        rows: dict[str, list[tuple[Any, ...]]] = {}
        with self.connection.cursor() as cursor:
            for target in targets:
                cursor.execute(
                    SAME_DAY_THESES,
                    (self._run.run_date, target.kind.value, target.code, self._run.as_of_at),
                )
                found = [(RunSlot(row[0]), *row[1:]) for row in cursor.fetchall()]
                if found:
                    rows[target.code] = found
        return rows

    # -- 실행 ----------------------------------------------------------------

    def run(self, *, dag_run_id: str, try_number: int) -> int:
        """휴장 판정 → readiness guard → 관측 상태 → 되짚기 → LLM → 저장. 저장한 행 수를 준다."""
        from modules.thesis.domain import PREFETCHED_PAST_THESES
        from modules.thesis.store import ThesisStore

        self._run.skip_unless_open()

        store = ThesisStore(self.connection)
        targets = store.subjects()
        bars = self.check_ready(targets)

        # 같은 대상의 지난 날 예측·리뷰와 그 채점·해설. 오늘 앞 슬롯은 별도 절이다.
        past = {
            target.code: store.past_theses(
                as_of_at=self._run.as_of_at,
                subject_code=target.code,
                n=PREFETCHED_PAST_THESES,
            )
            for target in targets
        }
        # 관측 상태를 두 번 만들지 않는다 — 축은 그 상태에서 파생하므로 값이 갈리면 안 된다.
        observed = self.observed_state(targets, bars)
        return self._run.build_and_store(
            try_number=try_number,
            run_kind=LlmRunKind.FORECAST,
            run_slot=self._slot,
            macro_window_start=self.macro_window_start(),
            targets=targets,
            observed=observed,
            past=past,
            dag_run_id=dag_run_id,
            same_day=self.same_day(targets, bars),
            baselines=intraday_baselines(observed),
        )


def intraday_baselines(observed: ObservedState) -> dict[str, ForecastBaseline]:
    """장중 예측의 축. 관측 상태의 `intraday`를 그대로 옮긴다.

    **`at`이 `as_of_at`이 아니라 봉의 `bar_at`이다.** 슬롯은 기준 시각 **직전** 봉을 보고
    수집이 밀리면 `BAR_STALENESS`(15분)까지 앞선 봉이라, `as_of_at`으로 적으면
    "12:35 기준"이라 써 놓고 12:20 값을 보여 주는 줄이 생긴다(2026-08-28 실측: `as_of_at`
    03:35Z, 코스피가 본 봉 03:30Z).

    `return_pct`는 전일 종가 대비 여기까지 온 등락이고 예측 크기와 **축이 다르다.**
    저장해 두면 읽는 쪽이 둘을 더해 하루 등락으로 읽을 수 있다.

    감쌀 상태가 없어 함수다. `common.session_baselines`의 장중 짝이고, 둘이 다른 모듈에
    있는 이유는 슬롯마다 다른 것을 슬롯별 모듈이 갖기 때문이다.
    """
    return {
        code: ForecastBaseline(
            price=Decimal(str(row.price)),
            at=row.bar_at,
            return_pct=Decimal(str(row.return_pct)),
        )
        for code, row in observed.intraday.items()
    }


def _bar_close(bars: dict[str, Bar] | None, code: str) -> Decimal | None:
    """봉 묶음에서 한 대상의 종가. 없으면 `None`."""
    if not bars:
        return None
    found = bars.get(code)
    return found[1] if found else None


def build() -> ThesisRunResult:
    """Airflow 태스크 진입점. 컨텍스트를 읽어 장중 전망 하나를 돌린다."""
    context = get_current_context()
    run_date = common.resolve_run_date(context)
    run_slot = resolve_slot(context)
    dag_run_id = str(context["dag_run"].run_id)
    # 재시도는 새 대화다. dag_run_id는 재시도에도 같아 이 칸이 없으면 구분할 수 없다.
    try_number = int(context["ti"].try_number)
    logger.info("building the %s thesis for %s", SLOT_LABELS[run_slot], run_date)

    with closing(common.connection()) as conn:
        written = IntradayForecast(conn, run_date=run_date, run_slot=run_slot).run(dag_run_id=dag_run_id, try_number=try_number)
    return ThesisRunResult(run_date=run_date, slot=run_slot, written=written)
