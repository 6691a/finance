"""뉴스·문서 평가 브리핑의 조회·렌더링.

창은 `assessed_at` 기준이다. `published_at`이 아닌 이유는 평가가 수집보다 늦게 따라오기
때문이고, 이 리포트가 답하는 질문이 "파이프라인이 방금 무엇을 평가했나"이기 때문이다.

**0건이어도 보낸다.** `document_assessment_hourly`는 `source_record`를 남기지 않는 DAG라
이 메시지가 평가 파이프라인의 유일한 생존 신호를 겸한다. 다만 그때는 LLM을 부르지 않는다.
쓸 값이 없는데 요약을 시키면 없는 이야기를 지어낸다.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Protocol, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict

from modules.briefing import blocks
from modules.sql import read_sql
from modules.utility import KST_TIMEZONE

BRIEFING_SUMMARY = read_sql("postgres", "document", "select_briefing_summary.sql")
BRIEFING_TOP = read_sql("postgres", "document", "select_briefing_top.sql")

# 조회 창. 발송 주기보다 넉넉해야 두 실행 사이에 평가된 문서가 어느 쪽에도 안 잡히는 일이 없다.
WINDOW_HOURS = 12

# 채널에 그릴 문서 수. 나머지는 버리는 것이 아니라 안 그리는 것이다.
TOP_DOCUMENTS = 5

DIRECTION_MARKS = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Any) -> object: ...

    def fetchone(self) -> Any: ...

    def fetchall(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class TopDocument(BaseModel):
    model_config = ConfigDict(frozen=True)

    document_id: int
    title: str
    source_slug: str
    direction: str | None = None
    value_score: int | None = None
    canonical_url: str
    reason: str | None = None
    tickers: tuple[str, ...] = ()


class DocumentSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    generated_at: AwareDatetime
    window_hours: int
    detected: int
    assessed: int
    positive: int
    negative: int
    neutral: int
    backlog: int
    oldest_pending: AwareDatetime | None = None
    top: tuple[TopDocument, ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.assessed == 0


def collect_summary(
    connection: Connection,
    now: datetime,
    window_hours: int = WINDOW_HOURS,
    top_documents: int = TOP_DOCUMENTS,
) -> DocumentSummary:
    since = now - timedelta(hours=window_hours)
    with connection.cursor() as cursor:
        cursor.execute(BRIEFING_SUMMARY, (since,))
        counts = cursor.fetchone()
        cursor.execute(BRIEFING_TOP, (since, top_documents))
        top = cursor.fetchall()

    return DocumentSummary(
        generated_at=now,
        window_hours=window_hours,
        detected=counts[0],
        assessed=counts[1],
        positive=counts[2],
        negative=counts[3],
        neutral=counts[4],
        backlog=counts[5],
        oldest_pending=counts[6],
        top=tuple(
            TopDocument(
                document_id=row[0],
                title=row[1],
                source_slug=row[2],
                direction=row[3],
                value_score=row[4],
                canonical_url=row[5],
                reason=row[6],
                tickers=tuple(row[7] or ()),
            )
            for row in top
        ),
    )


def render_blocks(summary: DocumentSummary, comment: str | None, error: str | None = None) -> list[dict[str, Any]]:
    local = summary.generated_at.astimezone(KST_TIMEZONE)
    rendered = [blocks.header(f"📰 문서 평가 브리핑 · {blocks.timestamp(local)}")]

    if summary.is_empty:
        rendered.append(
            blocks.section(f"최근 {summary.window_hours}시간 신규 평가 문서 없음 · 대기 {summary.backlog}건")
        )
        rendered.append(blocks.context(_as_of(summary)))
        return rendered

    rendered += blocks.table_section(
        f"최근 {summary.window_hours}시간",
        ("구분", "건수"),
        (
            ("신규 수집", str(summary.detected)),
            ("평가 완료", str(summary.assessed)),
            ("긍정", str(summary.positive)),
            ("부정", str(summary.negative)),
            ("중립", str(summary.neutral)),
            ("평가 대기", str(summary.backlog)),
        ),
    )
    if summary.top:
        rendered.append(
            blocks.section("*주요 문서*\n" + "\n".join(_document_line(document) for document in summary.top))
        )
    rendered += blocks.comment_blocks(comment, error)
    rendered.append(blocks.context(_as_of(summary)))
    return rendered


def render_text(summary: DocumentSummary) -> str:
    if summary.is_empty:
        return f"문서 평가 브리핑 · 신규 없음 · 대기 {summary.backlog}건"
    head = summary.top[0].title if summary.top else "주요 문서 없음"
    return f"문서 평가 브리핑 · 평가 {summary.assessed}건 · {head}"


def comment_input(summary: DocumentSummary) -> str:
    """LLM에 줄 입력. 제목·이유·태그까지만 준다. 본문은 넣지 않는다."""
    payload = {
        "as_of_kst": summary.generated_at.astimezone(KST_TIMEZONE).isoformat(),
        "window_hours": summary.window_hours,
        "counts": {
            "detected": summary.detected,
            "assessed": summary.assessed,
            "positive": summary.positive,
            "negative": summary.negative,
            "neutral": summary.neutral,
            "backlog": summary.backlog,
        },
        "top": [
            {
                "title": document.title,
                "source": document.source_slug,
                "direction": document.direction,
                "value_score": document.value_score,
                "reason": document.reason,
                "tickers": list(document.tickers),
            }
            for document in summary.top
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _document_line(document: TopDocument) -> str:
    mark = DIRECTION_MARKS.get(document.direction or "", "⚪")
    tags = f" `{'` `'.join(document.tickers)}`" if document.tickers else ""
    return f"{mark} *{document.value_score}* <{document.canonical_url}|{document.title}> — {document.source_slug}{tags}"


def _as_of(summary: DocumentSummary) -> list[str]:
    lines = [f"평가 대기 {summary.backlog}건"]
    if summary.oldest_pending:
        waiting = summary.generated_at - summary.oldest_pending
        lines.append(f"최장 대기 {waiting.total_seconds() / 3600:.1f}시간")
    return lines
