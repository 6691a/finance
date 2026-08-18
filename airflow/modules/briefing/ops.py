"""수집 운영 브리핑의 조회·렌더링.

`source_record` 한 테이블이 거의 모든 답을 준다. 창 안의 실행을 제공처별로 접고, 기대하는
제공처 목록과 대조해 조용한 쪽을 찾는다.

## source_record에 안 잡히는 둘

- **환율**(`exchange_rate_daily`): `exchange_rate`가 외부 DB 형태를 그대로 가져온 테이블이라
  `source_record_id`가 없다. 그래서 계보가 아니라 신선도(마지막 고시일)로 본다.
- **문서 평가**(`document_assessment_hourly`): 새로 수집하는 것이 아니라 이미 저장한 문서를
  읽는다. 그래서 밀린 건수(`assessed_at IS NULL`)로 본다.

이 둘을 빠뜨리면 운영 리포트가 초록인데 두 파이프라인이 며칠째 죽어 있을 수 있다.

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

from datetime import date, datetime, timedelta
from typing import Any, NamedTuple, Protocol, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict

from modules.briefing import blocks, documents
from modules.sql import read_sql
from modules.utility import KST_TIMEZONE

BRIEFING_WINDOW = read_sql("postgres", "source_record", "select_briefing_window.sql")
RECENT_FAILURES = read_sql("postgres", "source_record", "select_recent_failures.sql")
EXCHANGE_RATE_FRESHNESS = read_sql("postgres", "exchange_rate", "select_freshness.sql")

WINDOW_HOURS = 24

# 채널에 그릴 실패 건수. 더 있으면 Airflow 로그를 봐야 하는 상황이다.
RECENT_FAILURE_LIMIT = 5

# 환율 고시가 이보다 오래되면 수집이 멈춘 것으로 본다. 주말·공휴일 연휴를 건널 만큼은 둔다.
EXCHANGE_RATE_STALE_DAYS = 4

# 평가 대기가 이보다 많으면 태깅이 밀린 것이다. 매시 50건씩 처리하므로 몇 시간치가 넘는 값이다.
ASSESSMENT_BACKLOG_LIMIT = 200


class ExpectedSource(NamedTuple):
    """하루에 한 번은 돌아야 하는 수집원.

    `weekdays_only`는 주말에 조용해도 정상인 곳이다. 국내 시장과 공시는 주말에 열지 않아
    이 표시가 없으면 매주 토·일 거짓 경보가 뜬다.
    """

    name: str
    label: str
    weekdays_only: bool = False


# 문서 피드는 여기 넣지 않는다. `document_source` 테이블이 정하고 수십 개라, 코드 상수와
# DB 목록이 어긋나는 순간 거짓 경보가 된다. 피드의 건강은 2부 문서 브리핑이 본다.
EXPECTED_SOURCES: tuple[ExpectedSource, ...] = (
    ExpectedSource("fred", "미국 지표", weekdays_only=True),
    ExpectedSource("ecos", "국내 시장금리", weekdays_only=True),
    ExpectedSource("mof", "일본 국채", weekdays_only=True),
    ExpectedSource("boe", "영국 국채", weekdays_only=True),
    ExpectedSource("bbk", "독일 국채", weekdays_only=True),
    ExpectedSource("ecb", "유로 국채", weekdays_only=True),
    ExpectedSource("kis", "국내 시세·수급", weekdays_only=True),
    ExpectedSource("dart", "공시", weekdays_only=True),
    ExpectedSource("yahoo", "해외 시세"),
    ExpectedSource("nyse", "미국 거래일"),
)

EXPECTED_BY_NAME = {source.name: source for source in EXPECTED_SOURCES}

SATURDAY = 5


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Any) -> object: ...

    def fetchone(self) -> Any: ...

    def fetchall(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


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
    failures: tuple[FailureDetail, ...] = ()
    exchange_rate_latest: date | None = None
    exchange_rate_stale: bool = False
    assessment_backlog: int = 0

    @property
    def is_healthy(self) -> bool:
        return not (
            self.silent
            or self.failures
            or self.exchange_rate_stale
            or self.assessment_backlog > ASSESSMENT_BACKLOG_LIMIT
        )


def collect_summary(connection: Connection, now: datetime) -> OpsSummary:
    since = now - timedelta(hours=WINDOW_HOURS)
    with connection.cursor() as cursor:
        cursor.execute(BRIEFING_WINDOW, (since,))
        activity_rows = cursor.fetchall()
        cursor.execute(RECENT_FAILURES, (since, RECENT_FAILURE_LIMIT))
        failure_rows = cursor.fetchall()
        cursor.execute(EXCHANGE_RATE_FRESHNESS, ())
        freshness = cursor.fetchone()
        # 문서 평가는 source_record를 안 남긴다. 2부와 같은 질문이라 같은 쿼리를 쓴다.
        cursor.execute(documents.BRIEFING_SUMMARY, (since,))
        document_counts = cursor.fetchone()

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
    latest_rate_date = freshness[0] if freshness else None
    return OpsSummary(
        generated_at=now,
        window_hours=WINDOW_HOURS,
        activity=activity,
        silent=silent_sources(activity, now),
        failures=tuple(
            FailureDetail(source=row[0], source_key=row[1], started_at=row[2], detail=row[3]) for row in failure_rows
        ),
        exchange_rate_latest=latest_rate_date,
        exchange_rate_stale=_stale(latest_rate_date, now),
        assessment_backlog=document_counts[5] if document_counts else 0,
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
        ("소스", "실행", "실패", "건수"),
        _activity_rows(summary),
    )
    if summary.silent:
        names = ", ".join(f"{source.label}({source.name})" for source in summary.silent)
        rendered.append(blocks.section(f"*무소식*\n{names}"))
    if summary.exchange_rate_stale:
        rendered.append(blocks.section(f"*환율 정체*\n마지막 고시 {summary.exchange_rate_latest}"))
    if summary.assessment_backlog > ASSESSMENT_BACKLOG_LIMIT:
        rendered.append(blocks.section(f"*문서 평가 적체*\n대기 {summary.assessment_backlog}건"))
    if summary.failures:
        rendered.append(blocks.section("*최근 실패*\n" + "\n".join(_failure_line(item) for item in summary.failures)))

    rendered.append(
        blocks.context([f"평가 대기 {summary.assessment_backlog}건", f"환율 {summary.exchange_rate_latest}"])
    )
    return rendered


def render_text(summary: OpsSummary) -> str:
    if summary.is_healthy:
        return f"수집 운영 현황 · 최근 {summary.window_hours}시간 정상"
    problems = []
    if summary.silent:
        problems.append(f"무소식 {len(summary.silent)}곳")
    if summary.failures:
        problems.append(f"실패 {len(summary.failures)}건")
    if summary.exchange_rate_stale:
        problems.append("환율 정체")
    if summary.assessment_backlog > ASSESSMENT_BACKLOG_LIMIT:
        problems.append(f"평가 적체 {summary.assessment_backlog}건")
    return "수집 운영 현황 · " + " · ".join(problems)


def _activity_rows(summary: OpsSummary) -> list[tuple[str, str, str, str]]:
    """기대 소스는 한 줄씩, 나머지(문서 피드)는 한 줄로 접는다.

    피드는 `document_source` 테이블이 정하고 수십 개라 하나씩 그리면 표가 화면을 넘는다.
    """
    rows = [
        (EXPECTED_BY_NAME[item.source].label, f"{item.runs:,}", f"{item.failed:,}", f"{item.records:,}")
        for item in summary.activity
        if item.source in EXPECTED_BY_NAME
    ]
    others = [item for item in summary.activity if item.source not in EXPECTED_BY_NAME]
    if others:
        rows.append(
            (
                f"문서 피드({len(others)})",
                f"{sum(item.runs for item in others):,}",
                f"{sum(item.failed for item in others):,}",
                f"{sum(item.records for item in others):,}",
            )
        )
    return rows


def _failure_line(item: FailureDetail) -> str:
    local = item.started_at.astimezone(KST_TIMEZONE)
    return f"• `{item.source}` {item.source_key} ({local:%m/%d %H:%M}) {item.detail or ''}".rstrip()


def _stale(latest: date | None, now: datetime) -> bool:
    if latest is None:
        return True
    return (now.astimezone(KST_TIMEZONE).date() - latest).days > EXCHANGE_RATE_STALE_DAYS
