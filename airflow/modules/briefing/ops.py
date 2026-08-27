"""수집 운영 브리핑의 조회·렌더링.

`source_record` 한 테이블이 거의 모든 답을 준다. 창 안의 실행을 제공처별로 접고, 기대하는
제공처 목록과 대조해 조용한 쪽을 찾는다.

## source_record에 안 잡히는 하나

- **문서 평가**(`document_assessment_hourly`): 새로 수집하는 것이 아니라 이미 저장한 문서를
  읽는다. 그래서 밀린 건수(`assessed_at IS NULL`)로 본다.

이걸 빠뜨리면 운영 리포트가 초록인데 이 파이프라인이 며칠째 죽어 있을 수 있다.

## 알려진 한계

`running` 행을 쓰는 수집기가 없어 **매달린 DAG는 '실행 중'이 아니라 '부재'로 보인다.**
무소식 판정이 그 부재를 잡는 데까지가 이 리포트의 몫이다. 실행 중 상태가 필요해지면
Airflow 메타데이터를 봐야 한다.

## 올그린에도 보낸다

침묵이 정상 신호이면 고장으로 인한 침묵과 구분할 수 없다. 하루 한 번은 견딜 만한 소음이다.

## LLM을 부르지 않는다

다른 파트와 달리 `comment_input`이 없다. 표와 실패 목록이 이미 사실을 다 말하고, 나머지를
감시하는 리포트가 모델 호출에 기대면 모델이 죽은 날 감시도 같이 흔들린다.
"""

from datetime import datetime, timedelta
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict

from modules.briefing import blocks, documents
from modules.db import Connection, Cursor
from modules.sql import read_sql
from modules.thesis.state import FORECAST_SLOTS, NARRATED_SLOTS
from modules.utility import KST_TIMEZONE

BRIEFING_WINDOW = read_sql("postgres", "source_record", "select_briefing_window.sql")
RECENT_FAILURES = read_sql("postgres", "source_record", "select_recent_failures.sql")
THESIS_CALIBRATION = read_sql("postgres", "thesis_outcome", "select_calibration.sql")
THESIS_BACKLOG = read_sql("postgres", "thesis_outcome", "select_backlog.sql")

WINDOW_HOURS = 24

# 추론 품질을 볼 구간. 프롬프트와 모델이 바뀌면 옛 점수와 섞여 추이가 흐려지므로 짧게 둔다.
THESIS_WINDOW_DAYS = 28

# 채점·해설 지평. `modules/thesis/domain.py`의 같은 목록과 값이 같아야 한다. 그쪽을 import하지 않는
# 이유는 ops 브리핑이 LLM 층에 기대지 않기 위해서다 — 감시하는 쪽이 감시받는 쪽을 부르면
# 그쪽이 죽은 날 이 리포트도 같이 흔들린다.
THESIS_HORIZONS = (0, 1, 3, 5)

# 균등 확률(1/3씩)의 3-class Brier. 결과와 무관하게 이 값이라 예측력 비교의 baseline이다.
UNIFORM_BRIER = 0.667

# 채널에 그릴 실패 건수. 더 있으면 Airflow 로그를 봐야 하는 상황이다.
RECENT_FAILURE_LIMIT = 5

# 평가 대기가 이보다 많으면 태깅이 밀린 것이다. 매시 50건씩 처리하므로 몇 시간치가 넘는 값이다.
ASSESSMENT_BACKLOG_LIMIT = 200


class ExpectedSource(BaseModel):
    """하루에 한 번은 돌아야 하는 수집원.

    `weekdays_only`는 주말에 조용해도 정상인 곳이다. 국내 시장과 공시는 주말에 열지 않아
    이 표시가 없으면 매주 토·일 거짓 경보가 뜬다.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    label: str
    weekdays_only: bool = False


# 문서 피드는 여기 넣지 않는다. `document_source` 테이블이 정하고 수십 개라, 코드 상수와
# DB 목록이 어긋나는 순간 거짓 경보가 된다. 피드의 건강은 2부 문서 브리핑이 본다.
EXPECTED_SOURCES: tuple[ExpectedSource, ...] = (
    ExpectedSource(name="fred", label="미국 지표", weekdays_only=True),
    ExpectedSource(name="ecos", label="국내 시장금리", weekdays_only=True),
    ExpectedSource(name="mof", label="일본 국채", weekdays_only=True),
    ExpectedSource(name="boe", label="영국 국채", weekdays_only=True),
    ExpectedSource(name="bbk", label="독일 국채", weekdays_only=True),
    ExpectedSource(name="ecb", label="유로 국채", weekdays_only=True),
    ExpectedSource(name="kis", label="국내 시세·수급", weekdays_only=True),
    ExpectedSource(name="dart", label="공시", weekdays_only=True),
    ExpectedSource(name="yahoo", label="해외 시세"),
    ExpectedSource(name="nyse", label="미국 거래일"),
)

EXPECTED_BY_NAME = {source.name: source for source in EXPECTED_SOURCES}

SATURDAY = 5
class SourceActivity(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    runs: int
    succeeded: int
    failed: int
    records: int
    last_completed_at: AwareDatetime | None = None


class FailureDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    source_key: str
    started_at: AwareDatetime
    detail: str | None = None


class ThesisHorizon(BaseModel):
    """지평 하나의 추론 품질. 지평을 섞으면 T+1의 잡음이 T+5의 신호를 덮는다."""

    model_config = ConfigDict(frozen=True)

    horizon_days: int
    graded: int
    mean_brier: float | None
    flat_outcomes: int
    narrated: int
    supported: int
    contradicted: int
    unresolved: int
    # 크기 채점. **지평 0에만 있고** flat 실현·판 7 이전 행은 빠져서 표본이 `graded`와 다르다.
    # 평균은 부호를 살린다 — 양수면 과소추정, 음수면 과대추정이고 그것이 프롬프트를 고칠 방향이다.
    return_graded: int = 0
    mean_return_error_pct: float | None = None

    @property
    def beats_uniform(self) -> bool | None:
        """균등 확률 baseline보다 나은가. 채점이 없으면 `None`."""
        if self.mean_brier is None:
            return None
        return self.mean_brier < UNIFORM_BRIER


class ThesisHealth(BaseModel):
    """추론 파이프라인의 운영·품질 지표.

    **두 묶음을 섞지 않는다**(`docs/analysis/market-thesis/README.md` 5절). backlog는 운영 지표라
    이걸로 판단하고, Brier와 판정 분포는 누적만 한다 — 4주 표본으로 예측력을 결론 내리지 못한다.
    """

    model_config = ConfigDict(frozen=True)

    window_days: int = THESIS_WINDOW_DAYS
    horizons: tuple[ThesisHorizon, ...] = ()
    ungraded: int = 0
    unnarrated: int = 0

    @property
    def has_backlog(self) -> bool:
        """목표 영업일이 지났는데도 안 된 것이 있는가. 아직 안 지난 것은 세지 않는다."""
        return bool(self.ungraded or self.unnarrated)


class OpsSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: AwareDatetime
    window_hours: int
    activity: tuple[SourceActivity, ...] = ()
    silent: tuple[ExpectedSource, ...] = ()
    failures: tuple[FailureDetail, ...] = ()
    assessment_backlog: int = 0
    thesis: ThesisHealth = ThesisHealth()

    @property
    def is_healthy(self) -> bool:
        return not (
            self.silent
            or self.failures
            or self.assessment_backlog > ASSESSMENT_BACKLOG_LIMIT
            or self.thesis.has_backlog
        )


class OpsBriefingReader:
    """운영 리포트 한 통에 들어갈 값을 읽는다. 연결과 기준 시각이 상태다.

    `briefing.market_data.MarketBriefingReader`와 같은 모양이다. 조회 셋이 전부 그 둘을 쓰고,
    한 번의 발송 동안 둘 다 바뀌지 않는다.

    **렌더링과 판정은 여기 없다.** `render_blocks`·`render_text`와 `silent_sources`는
    읽은 값만 보고 답하므로 감쌀 상태가 없어 모듈 함수로 남는다.
    """

    def __init__(self, connection: Connection, now: datetime) -> None:
        self.connection = connection
        self.now = now

    def summary(self) -> OpsSummary:
        since = self.now - timedelta(hours=WINDOW_HOURS)
        with self.connection.cursor() as cursor:
            cursor.execute(BRIEFING_WINDOW, (since,))
            activity_rows = cursor.fetchall()
            cursor.execute(RECENT_FAILURES, (since, RECENT_FAILURE_LIMIT))
            failure_rows = cursor.fetchall()
            # 문서 평가는 source_record를 안 남긴다. 2부와 같은 질문이라 같은 쿼리를 쓴다.
            cursor.execute(documents.BRIEFING_SUMMARY, (since,))
            document_counts = cursor.fetchone()
            thesis = self._thesis_health(cursor)

        activity = tuple(
            SourceActivity(
                source=row[0],
                runs=row[1],
                succeeded=row[2],
                failed=row[3],
                records=row[4],
                last_completed_at=row[5],
            )
            for row in activity_rows
        )
        return OpsSummary(
            generated_at=self.now,
            window_hours=WINDOW_HOURS,
            activity=activity,
            silent=silent_sources(activity, self.now),
            failures=tuple(
                FailureDetail(source=row[0], source_key=row[1], started_at=row[2], detail=row[3])
                for row in failure_rows
            ),
            assessment_backlog=document_counts[5] if document_counts else 0,
            thesis=thesis,
        )

    def _thesis_health(self, cursor: Cursor) -> ThesisHealth:
        """추론 품질과 밀린 건수.

        **DB 오류를 삼키지 않는다.** `thesis_outcome`이 없다는 것은 마이그레이션이 안 됐다는
        뜻이고, 그건 운영 리포트가 조용히 넘길 일이 아니라 소리쳐야 할 일이다. 빈 섹션으로
        바꾸면 테이블이 없는 상태가 "아직 추론이 없다"와 구분되지 않는다.

        추론이 정말 없는 날은 조회가 0행을 주고 섹션이 안 그려진다. 그건 정상 흐름이다.
        """
        since = (self.now.astimezone(KST_TIMEZONE) - timedelta(days=THESIS_WINDOW_DAYS)).date()
        today = self.now.astimezone(KST_TIMEZONE).date()
        cursor.execute(THESIS_CALIBRATION, (since,))
        rows = cursor.fetchall()
        cursor.execute(
            THESIS_BACKLOG,
            (list(THESIS_HORIZONS), since, list(FORECAST_SLOTS), list(NARRATED_SLOTS), today),
        )
        backlog = cursor.fetchone()
        return ThesisHealth(
            horizons=tuple(
                ThesisHorizon(
                    horizon_days=row[0],
                    graded=row[1],
                    mean_brier=float(row[2]) if row[2] is not None else None,
                    flat_outcomes=row[3],
                    narrated=row[4],
                    supported=row[5],
                    contradicted=row[6],
                    unresolved=row[7],
                    return_graded=row[8],
                    mean_return_error_pct=float(row[9]) if row[9] is not None else None,
                )
                for row in rows
            ),
            ungraded=backlog[0] if backlog else 0,
            unnarrated=backlog[1] if backlog else 0,
        )


def silent_sources(activity: tuple[SourceActivity, ...], now: datetime) -> tuple[ExpectedSource, ...]:
    """창 안에 한 번도 안 돈 수집원. 주말에는 평일 전용 소스를 빼고 본다."""
    seen = {item.source for item in activity}
    weekend = now.astimezone(KST_TIMEZONE).weekday() >= SATURDAY
    return tuple(
        source for source in EXPECTED_SOURCES if source.name not in seen and not (weekend and source.weekdays_only)
    )


def render_blocks(summary: OpsSummary) -> list[dict[str, Any]]:
    local = summary.generated_at.astimezone(KST_TIMEZONE)
    mark = "✅" if summary.is_healthy else "⚠️"
    rendered = [blocks.header(f"{mark} 수집 운영 현황 · {blocks.timestamp(local)}")]

    if summary.is_healthy:
        rendered.append(blocks.section(f"최근 {summary.window_hours}시간 모든 수집 정상"))

    rendered += blocks.table_section(
        f"최근 {summary.window_hours}시간 수집",
        ("소스", "실행", "실패", "건수", "마지막"),
        _activity_rows(summary),
    )
    if summary.silent:
        names = ", ".join(f"{source.label}({source.name})" for source in summary.silent)
        rendered.append(blocks.section(f"*무소식*\n{names}"))
    if summary.assessment_backlog > ASSESSMENT_BACKLOG_LIMIT:
        rendered.append(blocks.section(f"*문서 평가 적체*\n대기 {summary.assessment_backlog}건"))
    if summary.failures:
        rendered.append(blocks.section("*최근 실패*\n" + "\n".join(_failure_line(item) for item in summary.failures)))

    rendered += _thesis_blocks(summary.thesis)
    rendered.append(blocks.context([f"평가 대기 {summary.assessment_backlog}건"]))
    return rendered


def _thesis_blocks(health: ThesisHealth) -> list[dict[str, Any]]:
    """추론 품질 섹션. 채점된 것이 하나도 없으면 아예 넣지 않는다.

    시장 브리핑에서 뺀 것이 여기 온다(2026-08-21). 읽는 사람이 달라서다 — 오늘 전망은
    시장을 보는 사람이 읽고, "우리 추론이 잘 맞고 있나"는 운영자가 본다.

    **해설 전문은 싣지 않는다.** 숫자가 이상할 때 DB를 열면 된다. 매일 해설 몇 편이
    운영 리포트에 쌓이면 정작 봐야 할 실패 목록이 묻힌다.
    """
    if not health.horizons and not health.has_backlog:
        return []

    rendered: list[dict[str, Any]] = []
    if health.horizons:
        rendered += blocks.table_section(
            f"추론 품질 · 최근 {health.window_days}일",
            ("지평", "채점", "Brier", "크기 오차", "판정(지지/반박/보류)"),
            [_horizon_row(item) for item in health.horizons],
        )
        # baseline을 매번 다시 설명하지 않도록 한 줄로 붙인다.
        rendered.append(
            blocks.context(
                [
                    f"균등 확률 baseline {UNIFORM_BRIER}",
                    "낮을수록 좋다",
                    "판정은 Brier와 다른 것을 잰다 — 저쪽은 방향, 이쪽은 이유",
                ]
            )
        )
    if health.has_backlog:
        # 목표 영업일이 지났는데도 안 된 것만 센다. 아직 안 지난 것은 정상이다.
        rendered.append(blocks.section(f"*추론 적체*\n미채점 {health.ungraded}건 · 미해설 {health.unnarrated}건"))
    return rendered


def _horizon_row(item: ThesisHorizon) -> tuple[str, str, str, str, str]:
    if item.mean_brier is None:
        brier = "-"
    else:
        # baseline보다 나은지를 기호로 갈라 준다. 숫자만 보면 매번 0.667과 비교해야 한다.
        mark = "✓" if item.beats_uniform else "✗"
        brier = f"{item.mean_brier:.3f} {mark}"
    if item.mean_return_error_pct is None:
        # 지평 1·3·5는 크기를 받지 않는다. 빈 칸이 정상이다.
        sizing = "-"
    else:
        # 부호가 뜻이다. 표본 수를 함께 적는다 — flat과 미채점이 빠져 Brier의 n과 다르다.
        gap = "과소" if item.mean_return_error_pct > 0 else "과대"
        sizing = f"{item.mean_return_error_pct:+.2f}%p {gap} (n={item.return_graded})"
    return (
        f"T+{item.horizon_days}",
        str(item.graded),
        brier,
        sizing,
        f"{item.supported}/{item.contradicted}/{item.unresolved}",
    )


def render_text(summary: OpsSummary) -> str:
    if summary.is_healthy:
        return f"수집 운영 현황 · 최근 {summary.window_hours}시간 정상"
    problems = []
    if summary.silent:
        problems.append(f"무소식 {len(summary.silent)}곳")
    if summary.failures:
        problems.append(f"실패 {len(summary.failures)}건")
    if summary.assessment_backlog > ASSESSMENT_BACKLOG_LIMIT:
        problems.append(f"평가 적체 {summary.assessment_backlog}건")
    if summary.thesis.has_backlog:
        problems.append(f"추론 적체 {summary.thesis.ungraded + summary.thesis.unnarrated}건")
    return "수집 운영 현황 · " + " · ".join(problems)


def _activity_rows(summary: OpsSummary) -> list[tuple[str, str, str, str, str]]:
    """기대 소스는 한 줄씩, 나머지(문서 피드)는 한 줄로 접는다.

    피드는 `document_source` 테이블이 정하고 수십 개라 하나씩 그리면 표가 화면을 넘는다.

    **마지막 열은 시각이 아니라 경과 시간이다.** 창이 24시간이라 `05:12`만 찍으면 오늘인지
    어제인지 읽는 사람이 뺄셈을 해야 한다. 무소식 섹션은 창 안에 한 번도 안 돈 소스만 잡으므로,
    "돌긴 돌았는데 20시간째 조용하다"를 보여 주는 자리가 이 열이다.

    **성공 열은 싣지 않는다.** 실행에서 실패를 빼면 나오는 값이라 한 칸을 더 쓸 값어치가 없다.
    """
    rows = [
        (
            EXPECTED_BY_NAME[item.source].label,
            f"{item.runs:,}",
            f"{item.failed:,}",
            f"{item.records:,}",
            _hours_ago(item.last_completed_at, summary.generated_at),
        )
        for item in summary.activity
        if item.source in EXPECTED_BY_NAME
    ]
    others = [item for item in summary.activity if item.source not in EXPECTED_BY_NAME]
    if others:
        latest = [item.last_completed_at for item in others if item.last_completed_at]
        rows.append(
            (
                f"문서 피드({len(others)})",
                f"{sum(item.runs for item in others):,}",
                f"{sum(item.failed for item in others):,}",
                f"{sum(item.records for item in others):,}",
                _hours_ago(max(latest) if latest else None, summary.generated_at),
            )
        )
    return rows


def _hours_ago(moment: datetime | None, now: datetime) -> str:
    """마지막 완료로부터 몇 시간이 지났는지. 완료한 실행이 없으면 `-`.

    창 안에서 방금 끝났으면 `0h`다. 음수는 시계 어긋남이라 0으로 접는다.
    """
    if moment is None:
        return "-"
    hours = int((now - moment).total_seconds() // 3600)
    return f"{max(hours, 0)}h"


def _failure_line(item: FailureDetail) -> str:
    local = item.started_at.astimezone(KST_TIMEZONE)
    return f"• `{item.source}` {item.source_key} ({local:%m/%d %H:%M}) {item.detail or ''}".rstrip()
