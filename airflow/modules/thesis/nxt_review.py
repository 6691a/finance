"""NXT 애프터마켓 리뷰(`post_nxt_close`)에만 쓰는 것. `market_thesis_nxt_review` DAG가 부른다.

여기 있는 것은 전부 "정규장이 닫힌 뒤"라서 그런 것이다. 다른 슬롯과 공유하지 않는다 —
공유하면 다시 `if slot ==`이 생긴다. 슬롯을 모르는 것은 `thesis/common.py`에 있다.

**애프터마켓 창·봉 행 모델·조회는 `thesis/common.py`에 있다**(2026-08-31 이동). NXT가
15:30~20:00이라는 것은 세션 사실이지 슬롯 사실이 아니고, 장전 전망이 전 영업일의 같은 창을
읽는다(`18-nxt-precedent.md` 2.6절). 여기 남은 것은 기준 시각·매크로 창·readiness guard처럼
"이 슬롯이라서" 그런 것뿐이다.

## 왜 슬롯을 또 나눴나 (2026-08-22)

한국 주식의 실제 하루는 KRX 15:30이 아니라 NXT 애프터마켓 20:00에 끝난다. 그런데 장후
리뷰(`market_thesis_review`)의 기준 시각은 15:30이고 그 이후 정보는 **일부러** 뺀다 —
재실행마다 근거가 달라지는 것을 막기 위해서다. 그래서 하루의 마지막 4시간 30분이 추론
기록에 아예 없었다. 기존 리뷰에 애프터 데이터를 얹으면 그 슬롯의 event-time cutoff가
깨지므로 슬롯을 나눈다. 설계는 `docs/analysis/market-thesis/7-nxt-review.md`에 있다.

## 클래스인 이유

**연결과 세션 날짜가 상태다.** 조회 셋(애프터 봉, 확정 종가, 추론 대상)이 전부 그 둘을
쓰므로 함수로 두면 인자에 매번 다시 들어간다 — 저장소 규칙이 지목하는 신호가 그것이다.
추론 대상과 애프터 봉은 두 번 이상 읽히므로 처음 한 번만 조회하고 들고 있는다.

**기준 시각 계산은 모듈 함수다.** `as_of`·`macro_window_start`는 날짜 하나를 받아 시각
하나를 주는 순수 계산이라 감쌀 상태가 없다. 그런 것을 클래스로 만들지 않는다.

## 대상이 종목뿐이다

**NXT에는 지수가 없다.** 지수를 대상에 넣으면 모델이 매번 "지수는 정규장 마감값이라
움직이지 않았다"를 쓰게 된다. 지수 등락률은 관측 상태에 맥락으로만 싣는다.

## 기준 시각이 실행 시각이 아니다

이 DAG는 21:00에 돌지만 `as_of`는 **20:00 마감**이다. 20:05 REST 백필이 잠정 봉을
확정으로 바꾸기를 기다리느라 늦게 돌 뿐, 모델이 보는 것은 애프터마켓 마감까지다.

## 채점도 해설도 없다

리뷰는 예측이 아니라 채점할 대상이 없다(기존 `post_close`와 같은 이유). 사후 해설은
아직 붙이지 않았고, 새 슬롯이 그 루프에 조용히 들어가지 않도록
`thesis_outcome/select_pending_narratives.sql`과 `select_backlog.sql`이 슬롯을 열거한다.
"""

import logging
from contextlib import closing
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from airflow.sdk import get_current_context
from airflow.sdk.exceptions import AirflowSkipException

from modules.thesis import common
from modules.thesis.common import AfterHoursBar
from modules.thesis.domain import LlmRunKind, ThesisSubjectKind
from modules.thesis.state import NxtObservedState, RunSlot, ThesisRunResult
from modules.utility import KST_TIMEZONE

if TYPE_CHECKING:
    # 런타임 import는 안 한다 — 타입 이름 하나 때문에 모듈을 끌고 올 이유가 없다.
    # `TYPE_CHECKING`은 런타임에 돌지 않는다.
    from modules.thesis.domain import Subject

logger = logging.getLogger(__name__)

SLOT = "post_nxt_close"


def as_of(run_date: date) -> datetime:
    """조회 창의 끝(UTC) = 그날 20:00 NXT 마감. **실행 시각이 아니다**(모듈 docstring)."""
    return datetime.combine(run_date, common.AFTER_HOURS_CLOSE, tzinfo=KST_TIMEZONE).astimezone(UTC)


def macro_window_start(run_date: date) -> datetime:
    """매크로 창의 시작 = 당일 15:30 KRX 마감.

    "정규장이 닫힌 뒤 무엇이 움직였나"가 이 창이다. 장후의 창(당일 09:00부터)과 다르다.
    이 구간에 움직이는 미국 선물·환율·금리가 애프터마켓 해석의 외생 변수다.

    DB를 보지 않는다 — 오늘 장이 열렸다는 것은 readiness guard가 이미 확인했다.
    """
    return datetime.combine(run_date, common.AFTER_HOURS_OPEN, tzinfo=KST_TIMEZONE).astimezone(UTC)


class NxtAfterHoursReview:
    """애프터마켓 리뷰 한 번. 연결과 세션 날짜를 들고 돈다(모듈 docstring "클래스인 이유").

    생성자는 이 실행 동안 안 변하는 것만 받는다. 조회 결과는 처음 한 번만 읽고 들고 있는다 —
    `check_ready`와 `observed_state`가 같은 값을 본다.
    """

    def __init__(self, connection: Any, *, run_date: date) -> None:
        self._run = common.ThesisRun(connection, run_date=run_date, as_of_at=as_of(run_date))
        self._connection = connection
        self._run_date = run_date
        self._bars: tuple[AfterHoursBar, ...] | None = None
        self._targets: tuple[Subject, ...] | None = None

    @property
    def targets(self) -> tuple["Subject", ...]:
        """이번 실행의 추론 대상. **종목뿐이다** — NXT에 지수가 없다(모듈 docstring)."""
        if self._targets is None:
            from modules.thesis.store import ThesisStore

            subjects = ThesisStore(self._connection).subjects()
            self._targets = tuple(s for s in subjects if s.kind is ThesisSubjectKind.STOCK)
        return self._targets

    @property
    def watched(self) -> list[str]:
        return [subject.code for subject in self.targets]

    @property
    def bars(self) -> tuple[AfterHoursBar, ...]:
        """종목마다 애프터마켓 마지막 봉 하나. 봉이 없는 종목은 결과에 없다."""
        if self._bars is None:
            self._bars = self._run.after_hours_bars(self._run_date, self.watched)
        return self._bars

    def check_ready(self) -> None:
        """확정 종가와 애프터마켓 봉이 추론을 세울 만큼 들어왔는지 본다.

        **skip과 재시도를 가르는 축은 "기다리면 풀리는가"다.**

        - 애프터 봉이 하나도 없으면 `AirflowSkipException`. 체결이 진짜 0인 날이 있을 수 있고
          (휴장 전날, 연휴) 수집 실패와 응답만으로 가를 수 없다. `kis_stock_minute_bars_daily`도
          0봉을 INFO로 넘긴다. 죽여 봐야 매일 같은 빨간 실행이 남을 뿐이다.
        - 봉이 있는데 전부 잠정(`is_final=false`)이면 `ThesisNotReady`. 21:00에 그렇다는 것은
          20:05 REST 백필이 아직 안 돌았다는 뜻이다. 그대로 추론하면 **첫 성공본 불변** 때문에
          나중에 REST가 값을 바로잡아도 잘못된 값 위의 추론이 영영 남는다.
        - 확정 종가가 없으면 `ThesisNotReady`. 애프터 등락률의 분모다.
        """
        if not self.bars:
            raise AirflowSkipException(f"no NXT after-hours bars for {self._run_date}")

        if not any(bar.all_final for bar in self.bars):
            raise common.ThesisNotReady(
                f"NXT bars for {self._run_date} are all provisional; the REST backfill has not run"
            )

        self._run.require_settled_closes(self.watched)

    def observed_state(self) -> NxtObservedState:
        """프롬프트에 주는 관측 상태. 정규장·애프터마켓·지수 맥락 셋이다.

        **정규장 등락을 함께 주는 이유**: 애프터 등락만 주면 "왜 애프터에서 더 빠졌나"를 말할
        수 없다. 정규장에서 이미 빠진 종목이 더 빠진 것과, 오른 종목이 빠진 것은 다른 이야기다.

        정규장 값은 `common.observed_state`가 만든 것을 그대로 쓴다 — 채점이 보는 것과
        같은 원본(18:10 확정 종가, 15:30 지수 마감 봉)이어야 한다.

        **지수는 `index_regular`라는 이름으로 준다.** subject가 아니라 맥락이라는 사실이 키에서
        보여야 모델이 지수에 대한 추론을 쓰지 않는다.
        """
        regular = self._run.observed_state(self._run_date, self.targets)
        return NxtObservedState(
            session=self._run_date,
            regular=regular.stock,
            # `is not None`이다. `if bar.return_pct`로 두면 **보합(정확히 0)인 종목이 통째로
            # 빠져** 모델이 그것을 '애프터마켓 데이터 없음'으로 읽는다.
            after_hours={
                bar.stock_code: common.after_hours_entry(bar) for bar in self.bars if bar.return_pct is not None
            },
            index_regular=regular.index,
            technical=regular.technical,
            flat_base_rate=regular.flat_base_rate,
        )

    def run(self, *, dag_run_id: str, try_number: int) -> int:
        """휴장 판정 → readiness guard → 관측 상태 → LLM → 저장. 저장한 행 수를 준다."""
        self._run.skip_unless_open()
        self.check_ready()
        return self._run.build_and_store(
            try_number=try_number,
            run_kind=LlmRunKind.NXT_REVIEW,
            run_slot=RunSlot.POST_NXT_CLOSE,
            macro_window_start=macro_window_start(self._run_date),
            targets=self.targets,
            observed=self.observed_state(),
            # 리뷰는 예측이 아니라 해석이다. 과거 예측 성적을 실어 줄 자리가 아니다(장후와 같다).
            past={},
            dag_run_id=dag_run_id,
        )


def build() -> ThesisRunResult:
    """Airflow 태스크 진입점. 컨텍스트를 읽어 리뷰 하나를 돌린다."""
    context = get_current_context()
    run_date = common.resolve_run_date(context)
    dag_run_id = str(context["dag_run"].run_id)
    # 재시도는 새 대화다. dag_run_id는 재시도에도 같아 이 칸이 없으면 구분할 수 없다.
    try_number = int(context["ti"].try_number)

    with closing(common.connection()) as conn:
        written = NxtAfterHoursReview(conn, run_date=run_date).run(dag_run_id=dag_run_id, try_number=try_number)
    return ThesisRunResult(run_date=run_date, slot=SLOT, written=written)
