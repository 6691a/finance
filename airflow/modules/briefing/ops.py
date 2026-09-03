"""수집 운영 브리핑의 조회·렌더링.

`source_record` 한 테이블이 거의 모든 답을 준다. 창 안의 실행을 제공처별로 접고, 기대하는
제공처 목록과 대조해 조용한 쪽을 찾는다.

## source_record에 안 잡히는 하나

- **문서 평가**(`document_assessment_hourly`): 새로 수집하는 것이 아니라 이미 저장한 문서를
  읽는다. 그래서 밀린 건수(`assessed_at IS NULL`)로 본다.

이걸 빠뜨리면 운영 리포트가 초록인데 이 파이프라인이 며칠째 죽어 있을 수 있다.

## 알려진 한계

`running` 행을 쓰는 수집기가 없어 **매달린 DAG는 '실행 중'이 아니라 '부재'로 보인다.**
무소식 판정이 그 부재를 잡고, **0건 판정이 "돌긴 돌았는데 하루 종일 빈" 소스를 잡는다**
(2026-09-01, G-53). 실행 중 상태가 필요해지면 Airflow 메타데이터를 봐야 한다.

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
from modules.db import Connection
from modules.sql import read_sql
from modules.utility import KST_TIMEZONE


class OpsQueryError(RuntimeError):
    """감시 조회가 계약을 안 지켰다. 재시도해도 같은 결과다.

    **0으로 메우지 않는 이유가 이 예외의 존재 이유다.** 이 리포트는 "무엇이 밀렸나"를
    보는 화면이라, 조회가 비었을 때 0을 찍으면 초록으로 보이고 아무도 안 본다.
    """


BRIEFING_WINDOW = read_sql("postgres", "source_record", "select_briefing_window.sql")
RECENT_FAILURES = read_sql("postgres", "source_record", "select_recent_failures.sql")

WINDOW_HOURS = 24

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


class OpsSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: AwareDatetime
    window_hours: int
    activity: tuple[SourceActivity, ...] = ()
    silent: tuple[ExpectedSource, ...] = ()
    # 돌긴 돌았는데 창 안에 0건인 기대 소스(`empty_sources`). 무소식과 따로 두는 이유는
    # 고칠 곳이 다르기 때문이다 — 무소식은 스케줄러·DAG, 0건은 수집기의 판정이다.
    empty: tuple[ExpectedSource, ...] = ()
    failures: tuple[FailureDetail, ...] = ()
    assessment_backlog: int = 0

    @property
    def is_healthy(self) -> bool:
        return not (
            self.silent
            or self.empty
            or self.failures
            or self.assessment_backlog > ASSESSMENT_BACKLOG_LIMIT
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
            if document_counts is None:
                # GROUP BY 없는 집계라 한 행이 반드시 온다. 안 오면 쿼리나 스키마가 깨진
                # 것이고, 그때 적체 0을 찍으면 **감시 리포트가 초록으로 위장한다.**
                raise OpsQueryError("document briefing summary returned no row")

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
            empty=empty_sources(activity, self.now),
            failures=tuple(
                FailureDetail(source=row[0], source_key=row[1], started_at=row[2], detail=row[3])
                for row in failure_rows
            ),
            assessment_backlog=document_counts[5],
        )



def silent_sources(activity: tuple[SourceActivity, ...], now: datetime) -> tuple[ExpectedSource, ...]:
    """창 안에 한 번도 안 돈 수집원. 주말에는 평일 전용 소스를 빼고 본다."""
    seen = {item.source for item in activity}
    weekend = now.astimezone(KST_TIMEZONE).weekday() >= SATURDAY
    return tuple(
        source for source in EXPECTED_SOURCES if source.name not in seen and not (weekend and source.weekdays_only)
    )


def empty_sources(activity: tuple[SourceActivity, ...], now: datetime) -> tuple[ExpectedSource, ...]:
    """돌긴 돌았는데 창 안에 한 건도 안 남긴 수집원. **성공으로 끝난 실행만 본다.**

    `succeeded`·`record_count=0`이 24시간 쌓여도 전에는 ✅였다(2026-08-31 조사 G-53). 이
    저장소의 조용한 실패가 정확히 그 모양이라 — 빈 칸을 0으로 읽은 것, 30행 상한, 개장일
    0봉 — ops가 잡을 수 있는 마지막 자리였다. 전부 실패한 소스는 실패 목록이 말하므로 여기
    안 넣는다. 주말은 장이 없어 0건이 정상이라 무소식과 같은 규칙으로 통째로 뺀다.

    # ponytail: 평일 0건이 정상인 소스가 나타나면 `ExpectedSource`에 표시 칸을 더한다.
    """
    if now.astimezone(KST_TIMEZONE).weekday() >= SATURDAY:
        return ()
    empty = {item.source for item in activity if item.succeeded > 0 and item.records == 0}
    return tuple(source for source in EXPECTED_SOURCES if source.name in empty)


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
    if summary.empty:
        names = ", ".join(f"{source.label}({source.name})" for source in summary.empty)
        rendered.append(blocks.section(f"*0건*\n{names} — 성공으로 돌았는데 하루 종일 한 건도 안 남겼다"))
    if summary.assessment_backlog > ASSESSMENT_BACKLOG_LIMIT:
        rendered.append(blocks.section(f"*문서 평가 적체*\n대기 {summary.assessment_backlog}건"))
    if summary.failures:
        rendered.append(blocks.section("*최근 실패*\n" + "\n".join(_failure_line(item) for item in summary.failures)))

    rendered.append(blocks.context([f"평가 대기 {summary.assessment_backlog}건"]))
    return rendered


def render_text(summary: OpsSummary) -> str:
    if summary.is_healthy:
        return f"수집 운영 현황 · 최근 {summary.window_hours}시간 정상"
    problems = []
    if summary.silent:
        problems.append(f"무소식 {len(summary.silent)}곳")
    if summary.empty:
        problems.append(f"0건 {len(summary.empty)}곳")
    if summary.failures:
        problems.append(f"실패 {len(summary.failures)}건")
    if summary.assessment_backlog > ASSESSMENT_BACKLOG_LIMIT:
        problems.append(f"평가 적체 {summary.assessment_backlog}건")
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
