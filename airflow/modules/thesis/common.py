"""장전·장후 두 추론 DAG가 **분기 없이** 함께 쓰는 것만 둔다.

여기 있는 것은 슬롯을 알 필요가 없다. 슬롯마다 달라지는 것(기준 시각, readiness guard,
매크로 창의 시작, 관측 상태의 세션 날짜)은 `thesis/forecast.py`와 `thesis/review.py`가
각각 갖는다.

**슬롯 문자열을 받아 `if`로 가르는 함수를 두지 않는다.** 전에는 DAG 하나가 `logical_date`의
시각으로 슬롯을 판정하고 태스크마다 그 값으로 갈라졌다. 두 갈래 중 한쪽만 도는 코드가
반씩 섞여 있어 읽을 때 매번 "지금 어느 쪽 이야기인가"를 따라가야 했고, 수동 실행에서
슬롯이 벽시계로 떨어지는 함정도 거기서 나왔다(2026-08-21 분리).

`dags/`에는 스케줄·재시도·태스크 배치만 남긴다(프로젝트 규칙).
**이 모듈은 Airflow를 import한다.** `modules/period.py`가 Airflow를 피하는 것과 다른데,
저쪽은 배포 환경 없이 수집기 테스트를 돌리기 위한 것이고 이 모듈을 쓰는 것은 DAG뿐이다.

LangChain·LangGraph import는 함수 안에서 한다(DagBag 30초 타임아웃, 2026-08-19 실측).
"""

import logging
import os
import re
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from types import MappingProxyType
from typing import Any

from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param
from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException
from pydantic import AwareDatetime, BaseModel, ConfigDict, SecretStr

from modules.market_session import krx_open_day
from modules.slack import SlackClient, SlackError
from modules.sql import read_sql
from modules.thesis.domain import (
    DOMESTIC_MAX_DAILY_CHANGE_PCT,
    MAX_TOOL_ROUNDS,
    PROMPT_VERSION,
    ForecastBaseline,
    LlmRunStatus,
    ThesisError,
    ThesisEvidenceKind,
    ThesisSubjectKind,
    evidence_ref,
)
from modules.thesis.state import (
    AfterHoursObservation,
    CausalChannel,
    CausalDirection,
    HorizonBaseRate,
    IndexObservation,
    NxtObservedState,
    ObservedState,
    PastThesis,
    SameDayThesis,
    SignalObservation,
    StockObservation,
    TechnicalObservation,
    TechnicalState,
    ThesisRunResult,
)
from modules.utility import CONNECTION_ID, KST_TIMEZONE

logger = logging.getLogger(__name__)

RUN_DATE_PARAM = "run_date"

# 관측 상태에 싣는 신호의 창과 개수. 툴(90일)보다 짧다 — 관측 상태는 "지금 상태"고 툴은
# "이력"이다. 개수를 묶는 이유는 프롬프트가 사건 목록으로 채워지지 않게 하기 위해서다.
SIGNAL_STATE_DAYS = 30
MAX_STATE_SIGNALS = 3

# 달력 하루만 받는다. ISO 주 표기(2026-W32)와 기본형(20260821)을 걸러 내는 그물이다.
CALENDAR_DAY_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")

# KRX 정규장 개장·마감(KST). 마감은 장후 슬롯의 기준 시각이자 지평 채점의 기준 시각이고,
# 개장은 "오늘 장중에 무엇이 움직였나"를 묻는 매크로 창의 시작이다(장후·장중이 함께 쓴다).
SESSION_OPEN_TIME = time(9, 0)
CLOSE_TIME = time(15, 30)

# NXT 애프터마켓의 양 끝(KST). 거래소만 걸면 프리·주간 세션이 섞인다 — 하루 690봉 중 애프터는
# 260봉뿐이다(2026-08-21 운영 DB 실측).
#
# **하한은 KRX 마감 15:30이고 실제 첫 애프터 봉은 15:40이다**(같은 실측: 15:40~19:59가
# 정확히 260분, 구멍 없음). 10분을 더 열어 두는 것은 그 사이에 봉이 없어 결과가 같고,
# NXT가 나중에 15:30부터 열면 코드를 고치지 않아도 잡히기 때문이다. 주간 세션 마지막 봉은
# 15:20이라 이 하한으로도 섞이지 않는다.
#
# 세션 사실이지 슬롯 사실이 아니라 여기 있다 — 애프터마켓 리뷰(당일)와 장전 전망(전 영업일)이
# 같은 창을 세션 날짜만 달리해 읽는다(`18-nxt-precedent.md` 2.6절).
AFTER_HOURS_OPEN = time(15, 30)
AFTER_HOURS_CLOSE = time(20, 0)

# 세 DAG가 같은 재시도 정책을 쓴다. 재시도 셋은 readiness guard가 선행 DAG의 지연을
# 기다리는 수단이다.
DEFAULT_ARGS: dict[str, Any] = {"retries": 3, "retry_delay": timedelta(minutes=10)}

# 추론 생성 태스크 한 번의 상한. 요청 하나의 타임아웃(`llm.THESIS_TIMEOUT_SECONDS`)은 모델
# 호출 한 번만 막고, 한 빌드는 조사 왕복(`thesis.MAX_TOOL_ROUNDS`+1)과 답변·교정까지 모델을
# 여러 번 부른다. 이것이 없으면 느린 실행이 몇 시간을 끌어도 Airflow는 기다린다. 장전 전망은
# 08:35에 시작해 09:00 개장 전에 닿아야 하므로 이 값이 그 쪽 기준이다. 재시도는 그대로 셋이다.
BUILD_TIMEOUT = timedelta(minutes=30)

SETTLED_CLOSE_COUNT = (
    "SELECT count(DISTINCT stock_code) FROM stock_investor_trade_daily "
    "WHERE provider = 'kis' AND business_date = %s AND stock_code = ANY(%s)"
)

AFTER_HOURS_STATE = read_sql("postgres", "stock_bar", "select_nxt_after_hours.sql")
CAUSAL_DIRECTION = read_sql("postgres", "market_causal_direction", "select_for_thesis.sql")

# 방향성이 얼마나 낡아도 되나. 정상은 `W-2`(9~15일 전)이고 한 주 놓치면 `W-3`이라 거기까지
# 허용한다. 그보다 오래면 **안 싣는다** — 주간 태스크가 skip되거나 실패했을 때 그 skip이
# 추론까지 전파되는 마지막 자리다(설계 §4.2.1).
MAX_DIRECTION_AGE_WEEKS = 3


class AfterHoursBar(BaseModel):
    """`select_nxt_after_hours.sql`이 주는 한 줄. 종목마다 애프터 마지막 봉 하나다.

    **컬럼 순서가 계약이다.** `from_row`가 그 순서를 아는 유일한 자리이고, 나머지 코드는
    이름으로만 읽는다 — SQL이 열을 늘려도 인덱스가 밀리는 사고가 여기서 멈춘다.
    """

    model_config = ConfigDict(frozen=True)

    stock_code: str
    last_bar_at: AwareDatetime
    last_close: Decimal
    bar_count: int
    all_final: bool
    # 그 세션 15:30 확정 종가. 아직 안 들어왔으면 None이고 등락률도 None이다.
    settled_close: Decimal | None
    return_pct: Decimal | None

    @classmethod
    def from_row(cls, row: tuple) -> "AfterHoursBar":
        return cls(
            stock_code=row[0],
            last_bar_at=row[1],
            last_close=row[2],
            bar_count=row[3],
            all_final=row[4],
            settled_close=row[5],
            return_pct=row[6],
        )


def after_hours_entry(bar: AfterHoursBar) -> AfterHoursObservation:
    """봉 하나를 프롬프트 칸으로. 등락률이 없는 종목은 부르는 쪽이 이미 걸렀다."""
    return AfterHoursObservation(
        close=float(bar.last_close),
        return_pct=round(float(bar.return_pct or 0), 2),
        last_bar_at=bar.last_bar_at,
        bars=bar.bar_count,
    )


def closed_records(toolbox: Any) -> Any:
    """저장 직전에 열린 기록을 닫고 목록을 준다.

    결과도 오류도 없는 행은 DB CHECK(`(result IS NULL) <> (error IS NULL)`)를 어긴다.
    sibling 예외로 **실행조차 못 한** 호출이 그렇게 남는다.
    """
    toolbox.close_open_records()
    return toolbox.tool_calls


class ThesisNotReady(RuntimeError):
    """선행 DAG의 데이터가 아직 없다. 재시도하면 풀릴 수 있다."""


class ThesisRun:
    """추론 한 번이 객체 하나다. **연결·세션 날짜·기준 시각이 상태다.**

    세 슬롯 모듈이 전부 이 셋을 들고 돈다. 전에는 조회·판정·저장 일곱 함수가 `conn`을
    각각 다시 받았고(저장소 최다 반복), `build_and_store`는 인자 아홉 개였다.
    기준 구현은 `thesis.nxt_review.NxtAfterHoursReview`다.

    **슬롯을 모른다.** 슬롯은 `build_and_store`의 인자로 흘러갈 뿐 여기서 분기하지 않는다.
    슬롯마다 다른 것(기준 시각 계산, readiness guard, 매크로 창의 시작)은 슬롯별 모듈이 갖는다.

    **`dag_run_id`는 생성자가 아니라 메서드 인자다.** 저장 한 번에만 쓰인다.
    """

    def __init__(self, connection: Any, *, run_date: date, as_of_at: datetime) -> None:
        self._connection = connection
        self._run_date = run_date
        self._as_of_at = as_of_at
        self._previous_open_day: date | None = None
        self._previous_read = False

    @property
    def connection(self) -> Any:
        """`thesis.ThesisStore`처럼 같은 연결을 쓰는 쪽에 넘길 때만 쓴다."""
        return self._connection

    @property
    def run_date(self) -> date:
        return self._run_date

    @property
    def as_of_at(self) -> datetime:
        return self._as_of_at

    def skip_unless_open(self) -> None:
        """휴장일이면 `AirflowSkipException`. 세 슬롯이 같은 판정을 쓴다.

        휴장 판정은 **모르면 돌린다.** 달력을 아직 못 채웠다는 이유로 진짜 거래일을 빠뜨리는
        것이 휴장일에 한 번 더 부르는 것보다 나쁘다. NXT 달력은 따로 없어 KRX 것을 본다
        (`market_session`에 NXT market_code가 없다).
        """
        if krx_open_day(self._connection, self._run_date) is False:
            raise AirflowSkipException(f"KRX is closed on {self._run_date}")

    def require_settled_closes(self, watched: Sequence[str]) -> None:
        """감시 종목 전부의 확정 종가가 들어왔는지 본다. 장후·애프터마켓 슬롯의 분모다.

        확정 종가는 `kis_investor_trade_daily`가 18:10에 채운다. 하나라도 빠지면
        `ThesisNotReady` — 기다리면 풀리는 것이라 skip이 아니다.
        """
        with self._connection.cursor() as cursor:
            cursor.execute(SETTLED_CLOSE_COUNT, (self._run_date, list(watched)))
            if cursor.fetchone()[0] < len(watched):
                raise ThesisNotReady(f"settled closes for {self._run_date} are not all in yet")

    def after_hours_bars(self, session: date, stock_codes: Sequence[str]) -> tuple[AfterHoursBar, ...]:
        """그 세션의 NXT 애프터마켓 마지막 봉, 종목마다 하나. 봉이 없는 종목은 결과에 없다.

        **어느 세션인지는 부르는 쪽이 정한다** — 애프터마켓 리뷰는 당일, 장전 전망은 전
        영업일이다. 봉이 0개인지, 잠정뿐인지를 어떻게 다룰지도 부르는 쪽의 판단이다(리뷰는
        기다리고 장전은 비운다).
        """
        window_start, window_end = after_hours_window(session)
        with self._connection.cursor() as cursor:
            cursor.execute(AFTER_HOURS_STATE, (window_start, window_end, session, list(stock_codes)))
            return tuple(AfterHoursBar.from_row(row) for row in cursor.fetchall())

    def previous_open_day(self) -> date | None:
        """직전 개장일. 달력이 아직 그날까지 안 채워졌으면 `None`이다.

        **한 번만 조회하고 들고 있는다.** 장전은 이 값을 매크로 창의 시작과 관측 세션 둘에
        쓰는데, 그 사이 `market_calendar_daily`가 행을 넣으면 두 답이 갈린다. 창은 어제
        마감부터인데 관측은 오늘 세션을 보는 상태가 되고, 그대로 프롬프트에 실린다.
        """
        if self._previous_read:
            return self._previous_open_day
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT session_date FROM market_session "
                "WHERE market_code = 'KRX' AND session_date < %s AND effective_open_day "
                "ORDER BY session_date DESC LIMIT 1",
                (self._run_date,),
            )
            row = cursor.fetchone()
        self._previous_open_day = row[0] if row else None
        self._previous_read = True
        return self._previous_open_day

    def origin_day(self, horizon_days: int) -> date | None:
        """이 세션이 T+N이 되는 추론일. 달력이 없으면 `None`."""
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT session_date FROM market_session "
                "WHERE market_code = 'KRX' AND session_date < %s AND effective_open_day "
                "ORDER BY session_date DESC OFFSET %s LIMIT 1",
                (self._run_date, horizon_days - 1),
            )
            row = cursor.fetchone()
        return row[0] if row else None

    def observed_state(self, session: date | None, targets: Any) -> ObservedState:
        """프롬프트에 주는 관측 상태. **전부 SQL이 계산한다.**

        **어느 세션을 볼지는 부르는 쪽이 정한다.** 장후는 당일, 장전은 전 영업일이고 그 판단은
        각자의 모듈에 있다. 여기서는 받은 날짜의 마감값만 읽는다. 채점과 같은 원본을 본다.

        기술적 관측(`technical`)을 함께 싣는다. 추론 대상이 곧 지표 대상이라 툴로만 두면
        모델이 호출 상한 중 대상 수만큼을 같은 조회에 쓰거나 아예 안 본다(기술지표 문서
        14.1절). 이력과 대상 밖 심볼은 그대로 `daily_history` 툴 몫이다.

        **맨 dict가 아니라 모델을 돌려준다.** 이 값은 프롬프트와 JSONB 컬럼 둘로 나가므로 키
        오타가 조용히 살아남으면 안 된다(`thesis.state` 모듈 docstring).
        """
        if session is None:
            return ObservedState()

        index: dict[str, IndexObservation] = {}
        stock: dict[str, StockObservation] = {}
        index_codes = [s.code for s in targets if s.kind is ThesisSubjectKind.INDEX]
        stock_codes = [s.code for s in targets if s.kind is ThesisSubjectKind.STOCK]
        with self._connection.cursor() as cursor:
            cursor.execute(
                "SELECT symbol, close, previous_close FROM index_bar "
                "WHERE provider = 'kis' AND bar_at = %s AND symbol = ANY(%s)",
                (close_at(session), index_codes),
            )
            for symbol, close, previous in cursor.fetchall():
                if not previous:
                    # 분모가 0이면 등락을 만들 수 없다. 다만 **로그는 남긴다** — 조용히 빠지면
                    # 모델이 그 지수를 "데이터 없음"으로 읽는 이유를 아무도 되짚지 못한다.
                    logger.warning("index %s has no usable previous close on %s; left out of the state", symbol, session)
                    continue
                index[symbol] = IndexObservation(
                    close=float(close),
                    return_pct=round(float((close - previous) / previous) * 100, 2),
                )
            cursor.execute(
                "SELECT stock_code, close_price FROM stock_investor_trade_daily "
                "WHERE provider = 'kis' AND business_date = %s AND stock_code = ANY(%s)",
                (session, stock_codes),
            )
            for code, close in cursor.fetchall():
                stock[code] = StockObservation(close=float(close))

        codes = [target.code for target in targets]
        return ObservedState(
            session=session,
            index=index,
            stock=stock,
            technical=self.technical_state(codes),
            flat_base_rate=self.flat_base_rate(codes),
            causal_direction=self.causal_direction(codes),
        )

    def causal_direction(self, subject_codes: Sequence[str]) -> dict[str, CausalDirection]:
        """대상별 주간 인과 방향성. 설계는 `17-graph-query.md` §4다.

        **툴이 아니라 관측 상태다.** 추론 대상이 곧 그래프 대상이라 툴로 두면 모델이 호출
        상한 중 대상 수만큼을 같은 조회에 쓰거나 아예 안 본다 — `technical`이 여기 있는 것과
        같은 판단이다.

        **컷오프가 둘이다.** `created_at <= as_of_at`은 event-time cutoff이고,
        `week_start >= oldest_week`은 나이 상한이다(§4.2.1). 뒤엣것이 없으면 주간 태스크가
        skip돼도 지난 주 행이 남아 추론이 그것을 최신인 척 읽는다 — skip이 추론까지
        전파되지 않는다.

        상한 밖이거나 그 주에 경로가 없던 대상은 **키가 아예 없다.** 낡은 값을 "참고로"
        주지 않는다 — 며칠 전 것인지 적어 줘도 모델은 있는 값을 쓴다.
        """
        codes = list(subject_codes)
        if not codes:
            return {}
        oldest_week = self._as_of_at.astimezone(KST_TIMEZONE).date() - timedelta(
            weeks=MAX_DIRECTION_AGE_WEEKS
        )
        found: dict[str, CausalDirection] = {}
        with self._connection.cursor() as cursor:
            cursor.execute(
                CAUSAL_DIRECTION,
                {"codes": codes, "as_of_at": self._as_of_at, "oldest_week": oldest_week},
            )
            for row in cursor.fetchall():
                found[row[0]] = CausalDirection(
                    week_start=row[1],
                    bias=row[2],
                    reasoning=row[3],
                    up_count=row[4],
                    down_count=row[5],
                    flat_count=row[6],
                    path_ids=tuple(row[7] or ()),
                    channels=tuple(CausalChannel(**channel) for channel in (row[8] or ())),
                )
        return found

    def flat_base_rate(self, subject_codes: Sequence[str]) -> dict[str, HorizonBaseRate]:
        """대상별 `flat` 기준선. 최근 `FLAT_BASE_RATE_BARS`봉의 하루 등락 분포다.

        프롬프트가 상수로 들고 있던 값이다(`FLAT_BASE_RATE_PCT`, 2026-08-26 제거). 그 비율이
        연도별로 단조 감소해 6개월 만에 낡았고, 사람이 다시 재기 전까지 모델이 틀린 기준선을
        받았다. 실행마다 재면 체제가 바뀌어도 값이 따라간다.

        슬롯으로 갈리지 않는다 — 기준 시각은 부르는 쪽이 이미 정해서 넘겼다.
        """
        from modules.technical import base_rate

        codes = list(subject_codes)
        if not codes:
            return {}
        return base_rate.flat_base_rates(
            self._connection,
            as_of_date=self._as_of_at.astimezone(KST_TIMEZONE).date(),
            symbols=codes,
        )

    def technical_state(self, subject_codes: Sequence[str]) -> TechnicalState:
        """추론 대상의 기술적 관측. 지표 다섯 칸과 최근 신호다(문서 14.1절).

        **절대값이 아니라 비율로 준다.** `sma20=3160.2`를 주면 모델이 종가와 비교하는 계산을
        해야 하고, 그 계산은 틀릴 수 있다. 절대값이 필요하면 `daily_history` 툴이 있다.

        지표를 못 내는 대상은 `None`이다. 빈 dict나 0으로 채우면 모델이 "지표가 중립"으로 읽는다.

        슬롯으로 갈리지 않는다 — 세션과 기준 시각은 부르는 쪽이 이미 정해서 넘겼다.
        """
        from modules.technical import base_rate, indicators

        # SQL 상수는 툴박스가 갖고 그 모듈이 LangChain을 끈다. 늦게 import한다.
        from modules.thesis.toolbox import DAILY_HISTORY, RECENT_SIGNALS

        codes = list(subject_codes)
        if not codes:
            return TechnicalState()

        as_of_at = self._as_of_at
        # 사건마다 "그 신호가 과거에 어떻게 끝났나"를 붙인다. 사건만 주고 확률을 요구하면
        # 모델이 서사로 빈도를 지어낸다(10-base-rate.md 5절). 조회는 한 번이고 (심볼,
        # 종류, 방향) 키로 나눠 쓴다 — 신호마다 SQL을 부르면 왕복이 사건 수만큼 는다.
        rates = base_rate.signal_base_rates(
            self._connection,
            as_of_date=as_of_at.astimezone(KST_TIMEZONE).date(),
            symbols=codes,
        )
        with self._connection.cursor() as cursor:
            cursor.execute(
                DAILY_HISTORY,
                {
                    "symbols": codes,
                    # 대상은 부르는 쪽이 이미 정했다. watched를 다시 끌어오면 추론하지 않는
                    # 종목의 지표가 프롬프트에 실린다.
                    "include_watched": False,
                    "as_of_at": as_of_at,
                    "limit": indicators.TECHNICAL_LOOKBACK_BARS,
                },
            )
            rows = list(cursor.fetchall())

            # 신호는 대상마다 따로 묻는다. 추론 툴과 **같은 SQL**이라 cutoff 규칙이 어긋날 수
            # 없고, 대상이 두셋뿐이라 왕복을 아끼려고 파일을 하나 더 만들 값어치가 없다.
            signals: dict[str, tuple[SignalObservation, ...]] = {}
            for code in codes:
                cursor.execute(
                    RECENT_SIGNALS,
                    {
                        "symbol": code,
                        "since_date": (as_of_at - timedelta(days=SIGNAL_STATE_DAYS)).date(),
                        "as_of_at": as_of_at,
                        "limit": MAX_STATE_SIGNALS,
                    },
                )
                signals[code] = tuple(
                    SignalObservation(
                        # 인용할 수 있게 ref를 붙인다. 툴이 준 ref와 같은 모양이다.
                        ref=evidence_ref(ThesisEvidenceKind.TECHNICAL_SIGNAL, str(row[0])),
                        signal_date=row[2],
                        kind=str(row[3]),
                        direction=str(row[4]),
                        base_rate=rates.get((code, str(row[3]), str(row[4]))),
                    )
                    for row in cursor.fetchall()
                )

        grouped: dict[str, list[Any]] = {}
        for row in rows:
            grouped.setdefault(str(row[1]), []).append(row)

        subjects: dict[str, TechnicalObservation | None] = {}
        as_of_dates = []
        for code in codes:
            subject_rows = grouped.get(code)
            if not subject_rows:
                subjects[code] = None
                continue
            ascending = list(reversed(subject_rows))
            snapshot = indicators.summarize(
                code,
                str(ascending[0][2] or code),
                [
                    indicators.DailyBar(
                        business_date=row[5],
                        open=float(row[6]),
                        high=float(row[7]),
                        low=float(row[8]),
                        close=float(row[9]),
                        volume=None if row[10] is None else int(row[10]),
                    )
                    for row in ascending
                ],
                max_abs_daily_change_pct=DOMESTIC_MAX_DAILY_CHANGE_PCT,
            )
            if snapshot is None:
                subjects[code] = None
                continue
            as_of_dates.append(snapshot.as_of_date)
            subjects[code] = TechnicalObservation(
                close_vs_sma20_pct=round((snapshot.close / snapshot.sma20 - 1) * 100, 2),
                sma20_vs_sma60_pct=round((snapshot.sma20 / snapshot.sma60 - 1) * 100, 2),
                rsi14=round(snapshot.rsi14, 1),
                macd_histogram=round(snapshot.macd_histogram, 2),
                volume_ratio20=None if snapshot.volume_ratio20 is None else round(snapshot.volume_ratio20, 2),
                recent_signals=signals.get(code, ()),
            )

        # 사건 이름(골든크로스 등)을 여기서 붙이지 않는다. 모델은 kind·direction을 그대로 읽고,
        # 사람이 읽는 표기는 Slack 표와 툴 근거 제목이 갖는다.
        return TechnicalState(as_of_date=max(as_of_dates) if as_of_dates else None, subjects=subjects)

    def build_and_store(
        self,
        *,
        run_slot: Any,
        macro_window_start: datetime,
        targets: Any,
        observed: ObservedState | NxtObservedState,
        past: Mapping[str, Sequence[PastThesis]],
        dag_run_id: str,
        try_number: int,
        run_kind: Any,
        same_day: Mapping[str, Sequence[SameDayThesis]] = MappingProxyType({}),
        baselines: Mapping[str, ForecastBaseline] = MappingProxyType({}),
    ) -> int:
        """추론을 만들고 저장한다. 저장한 행 수를 준다.

        **슬롯으로 갈라지지 않는다.** 슬롯은 값으로 흘러갈 뿐이고, 무엇이 다른지(기준 시각,
        창의 시작, 관측 세션, 프롬프트에 실을 과거 추론 `past`와 오늘 앞 슬롯 `same_day`)는
        이미 부르는 쪽이 정해서 인자로 넘겼다. `past`는 subject 코드별
        `thesis.store.ThesisStore.past_theses` 행이고, 그 `id`가 `thesis_precedent` 엣지로 남는다.

        **`same_day`는 엣지를 남기지 않는다.** 그 행들은 저장된 채점이 아니라 봉에서 계산한
        중간 경과이고, `thesis_precedent`는 "무엇을 보고 냈나"가 아니라 "어느 과거 추론을
        보여 줬나"를 남기는 자리다. 같은 날 앞 슬롯은 `run_date`로 이미 이어져 있다.

        **첫 성공본 불변.** 행이 있으면 모델을 부르지 않는다 — LLM은 재호출마다 답이 달라서
        덮어쓰면 최초 판단이 사라진다.

        `baselines`는 subject 코드별 예측의 축(`ForecastBaseline`)이고 `thesis`의
        `base_price`·`base_at`·`base_return_pct` 셋이 된다. **예측 슬롯만 넘긴다** —
        장후 둘은 채점 대상이 아니라 예측의 축이 없다. 여기서 슬롯으로 분기하지 않는다:
        무엇을 넘길지는 이미 부르는 쪽이 정했다.
        """
        # LangChain·LangGraph를 끄는 모듈은 여기서 늦게 import한다(DagBag 30초 타임아웃).
        # `thesis.domain`은 가벼워서 모듈 수준에 있다.
        from modules.llm import LlmError, RetryableLlmError, model_name, thesis_model
        from modules.thesis.generation import ThesisBuilder
        from modules.thesis.store import ThesisStore
        from modules.thesis.toolbox import ThesisToolbox

        store = ThesisStore(self._connection)
        stored = store.existing_theses(run_date=self._run_date, run_slot=run_slot)
        if stored:
            logger.info("thesis for %s %s already exists; skipping the model", self._run_date, run_slot.value)
            return 0

        # 캐시는 서버마다라 같은 대화를 같은 서버로 보내야 한다(`modules/llm.py`). 날짜와
        # 슬롯이면 재시도도 같은 대화로 이어진다 — 난수를 쓰면 재시도가 캐시를 버린다.
        model = thesis_model(f"thesis-{self._run_date}-{run_slot.value}")
        toolbox = ThesisToolbox(
            self._connection,
            as_of_at=self._as_of_at,
            macro_window_start=macro_window_start,
            watched_codes=[s.code for s in targets if s.kind is ThesisSubjectKind.STOCK],
            subject_codes=[s.code for s in targets],
        )
        # 원장(13단계)을 **그래프 호출 전에** 연다. 대화가 죽어도 "시작했다"는 사실과
        # 그때까지 부른 툴이 남아야 한다 — 실패한 대화가 안 남으면 패턴 분석이 성공한
        # 실행만 보게 된다. 판단 저장과 다른 트랜잭션이다.
        llm_run_id = store.start_llm_run(
            kind=run_kind,
            run_date=self._run_date,
            run_slot=run_slot,
            as_of_at=self._as_of_at,
            dag_run_id=dag_run_id,
            try_number=try_number,
            llm_model=model_name(model),
            prompt_version=PROMPT_VERSION,
        )
        builder = ThesisBuilder(model, toolbox)
        try:
            investigation = builder.run(
                run_slot=run_slot,
                as_of_at=self._as_of_at,
                subjects=targets,
                observed_state=observed,
                past_theses=past,
                same_day=same_day,
            )
        # 넓게 잡되 **반드시 다시 올린다.** 잡는 이유는 원장을 닫는 것 하나뿐이다.
        except BaseException as error:
            store.finish_llm_run(
                llm_run_id,
                status=LlmRunStatus.FAILED,
                records=closed_records(toolbox),
                tool_rounds=toolbox.round_count,
                # 죽은 대화는 절단 여부를 모른다. 왕복 수가 상한이면 그쪽에서 읽는다.
                investigation_truncated=toolbox.round_count >= MAX_TOOL_ROUNDS,
                subjects_requested=len(targets),
                subjects_answered=0,
                usage=builder.usage,
                error=f"{type(error).__name__}: {error}",
            )
            if isinstance(error, ThesisError):
                raise AirflowFailException(str(error)) from error
            # 재시도할 값어치가 있는 것은 그대로 올린다. 판단은 여기서 한다.
            if isinstance(error, LlmError) and not isinstance(error, RetryableLlmError):
                raise AirflowFailException(str(error)) from error
            raise
        store.finish_llm_run(
            llm_run_id,
            status=LlmRunStatus.SUCCEEDED,
            records=closed_records(toolbox),
            tool_rounds=toolbox.round_count,
            investigation_truncated=investigation.truncated,
            subjects_requested=investigation.subjects_requested,
            subjects_answered=len(investigation.drafts),
            usage=builder.usage,
        )

        rows = store.store_theses(
            run_date=self._run_date,
            run_slot=run_slot,
            as_of_at=self._as_of_at,
            dag_run_id=dag_run_id,
            drafts=investigation.drafts,
            registry=toolbox.registry,
            observed_state=observed,
            llm_model=model_name(model),
            tool_rounds=investigation.tool_rounds,
            llm_run_id=llm_run_id,
            precedents={code: [row.id for row in rows] for code, rows in past.items()},
            baselines=baselines,
        )
        logger.info(
            "stored %s theses for %s %s (%s tool rounds%s)",
            len(rows),
            self._run_date,
            run_slot.value,
            investigation.tool_rounds,
            ", truncated" if investigation.truncated else "",
        )
        return len(rows)


def run_date_param() -> dict[str, Param]:
    """네 DAG가 같은 Param 하나를 쓴다."""
    return {
        RUN_DATE_PARAM: Param(
            None,
            type=["null", "string"],
            format="date",
            title="대상 세션 날짜",
            description="YYYY-MM-DD. 비우면 스케줄된 시각의 KST 날짜. 지난 날을 다시 만들 때만 준다.",
        ),
    }


def connection() -> Any:
    # 반환 타입은 provider 버전에 따라 psycopg2/psycopg3 래퍼로 갈린다. 어느 쪽이든 PEP 249다.
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def slack_settings() -> tuple[SecretStr, str]:
    token = os.environ.get("SLACK_BOT_TOKEN")
    channel = os.environ.get("SLACK_CHANNEL_MARKET")
    if not token or not channel:
        # 설정 누락이라 재시도해도 같다. 값 자체는 메시지에 넣지 않는다.
        raise AirflowFailException("SLACK_BOT_TOKEN and SLACK_CHANNEL_MARKET are required")
    return SecretStr(token), channel


def resolve_run_date(context: Any) -> date:
    """이 실행이 대상으로 삼는 세션 날짜(KST).

    **모양을 먼저 본다.** `date.fromisoformat`은 `2026-W32`도 받아 그 주의 월요일이 된다.
    운영자가 넣은 값과 다른 날을 조용히 추론하게 된다.
    """
    given = (context.get("params") or {}).get(RUN_DATE_PARAM)
    if given:
        text = str(given).strip()
        if not CALENDAR_DAY_PATTERN.fullmatch(text):
            raise AirflowFailException(f"{RUN_DATE_PARAM} must be YYYY-MM-DD, got {given!r}")
        return date.fromisoformat(text)
    logical = context.get("logical_date") or datetime.now(UTC)
    return logical.astimezone(KST_TIMEZONE).date()


def close_at(day: date) -> datetime:
    """그 날의 KRX 마감 시각(UTC)."""
    return datetime.combine(day, CLOSE_TIME, tzinfo=KST_TIMEZONE).astimezone(UTC)


def after_hours_window(day: date) -> tuple[datetime, datetime]:
    """그날 NXT 애프터마켓 봉을 고를 창(UTC). KST 경계 계산은 SQL이 아니라 여기서 한다."""
    return (
        datetime.combine(day, AFTER_HOURS_OPEN, tzinfo=KST_TIMEZONE).astimezone(UTC),
        datetime.combine(day, AFTER_HOURS_CLOSE, tzinfo=KST_TIMEZONE).astimezone(UTC),
    )


def open_at(day: date) -> datetime:
    """그 날의 KRX 개장 시각(UTC). 장후·장중의 매크로 창이 여기서 시작한다.

    DB를 보지 않는다 — 오늘 장이 열렸다는 것은 부르는 쪽의 readiness guard가 이미 확인했다.
    """
    return datetime.combine(day, SESSION_OPEN_TIME, tzinfo=KST_TIMEZONE).astimezone(UTC)


def session_baselines(observed: ObservedState, session: date) -> dict[str, ForecastBaseline]:
    """장전 예측의 축. **이미 조회한 관측 상태에서 파생한다 — 쿼리를 새로 만들지 않는다.**

    새로 조회하면 모델이 본 값과 저장한 값이 갈릴 수 있다(`intraday.check_ready`가 본 봉
    dict를 그대로 넘기는 것과 같은 이유).

    `return_pct`가 **정의상 0**이다. 기준가가 곧 직전 세션 확정 종가라 그 사이에 온 것이
    없다. 장중과 달리 "오늘 여기까지"가 비어 있는 것이 맞다.

    감쌀 상태가 없어 함수다. 지수는 `close`, 종목은 `close_price`가 원본이고 둘 다
    `ThesisRun.observed_state`가 채점과 같은 자리에서 읽어 온 값이다.
    """
    moment = close_at(session)
    baselines = {
        code: ForecastBaseline(price=Decimal(str(row.close)), at=moment, return_pct=Decimal(0))
        for code, row in ({**observed.index, **observed.stock}).items()
    }
    return baselines


def notify_slack(built: dict[str, Any]) -> str:
    """이번 슬롯의 추론을 보낸다. LLM을 다시 부르지 않는다.

    **채점과 해설은 여기 싣지 않는다.** 읽는 사람이 다르다 — 이 메시지는 오늘 시장을
    보는 사람이 읽고, "우리 추론이 잘 맞고 있나"는 운영자가 본다. 지표는
    `slack_ops_briefing`이 OPS 채널로 낸다.

    두 DAG가 같은 함수를 쓴다. 렌더링이 슬롯으로 갈리는 것은 `thesis.render.render_blocks`
    안이고, 그건 문구를 고르는 일이지 흐름이 갈리는 것이 아니다.
    """
    from modules.thesis.render import render_blocks, render_text
    from modules.thesis.store import ThesisStore

    token, channel = slack_settings()
    result = ThesisRunResult.model_validate(built)
    run_date = result.run_date
    run_slot = result.slot

    with closing(connection()) as conn:
        store = ThesisStore(conn)
        theses = store.existing_theses(run_date=run_date, run_slot=run_slot)
        ids = [thesis.id for thesis in theses]
        evidence = store.top_evidence(ids)

    blocks = render_blocks(run_slot, run_date, theses, evidence)
    text = render_text(run_slot, run_date, theses)
    try:
        return SlackClient(token).post_message(channel, text=text, blocks=blocks)
    except SlackError as error:
        # 토큰·채널·블록이 틀렸다. 다시 보내도 같은 결과다.
        raise AirflowFailException(str(error)) from error
