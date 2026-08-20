"""뉴스·문서 평가 브리핑의 조회·렌더링.

창은 `assessed_at` 기준이다. `published_at`이 아닌 이유는 평가가 수집보다 늦게 따라오기
때문이고, 이 리포트가 답하는 질문이 "파이프라인이 방금 무엇을 평가했나"이기 때문이다.

**0건이어도 보낸다.** `document_assessment_hourly`는 `source_record`를 남기지 않는 DAG라
이 메시지가 평가 파이프라인의 유일한 생존 신호를 겸한다. 다만 그때는 LLM을 부르지 않는다.
쓸 값이 없는데 고르라고 시키면 없는 이야기를 지어낸다.

## 여기는 후보만 뽑는다

`value_score` 상위 몇십 건을 후보로 뽑아 놓고, 그중 무엇을 그릴지는 `picks.py`가 목록
전체를 한 번에 보고 정한다. 점수는 후보를 자르는 데까지만 쓴다. 상위 구간은 거의 동점이라
그 안의 순서에 뜻이 없기 때문이고, 이유는 `picks.py` docstring에 있다. 선별이 실패하면
그때만 점수 순서로 떨어진다.
"""

import json
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any, Protocol, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict

from modules.briefing import blocks
from modules.briefing.picks import Pick
from modules.sql import read_sql
from modules.utility import KST_TIMEZONE

BRIEFING_SUMMARY = read_sql("postgres", "document", "select_briefing_summary.sql")
BRIEFING_CANDIDATES = read_sql("postgres", "document", "select_briefing_candidates.sql")

# 기본 조회 창. 실제 발송은 `window_hours_at`이 직전 발송 슬롯부터 지금까지로 계산한다.
# 시장에 바로 반영되는 기사(예: 자사주 매입 공시)가 다음날 아침에야 실리면 늦기 때문에
# 하루 한 번이 아니라 여러 번, 겹치지 않는 창으로 보낸다.
WINDOW_HOURS = 24

# 발송 시각(KST). 장 전 08:00, 점심 12:00, KRX 마감 15:30, NXT 마감 20:00.
# **DAG 스케줄과 같은 목록이어야 한다** — 창이 이 슬롯 사이를 이어서 덮는다.
SEND_SLOTS_KST = ((8, 0), (12, 0), (15, 30), (20, 0))

# 선별에 올릴 후보 수. 모델에 실리는 토큰의 상한이기도 하다. 하루 평가량이 이보다 적으면
# 전부 올라간다.
CANDIDATE_DOCUMENTS = 60

# 선별이 실패했을 때 점수 순서로 그리는 건수. 예전 방식으로 떨어지는 자리다.
FALLBACK_DOCUMENTS = 5

DIRECTION_MARKS = {"positive": "🟢", "negative": "🔴", "neutral": "⚪"}


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Any) -> object: ...

    def fetchone(self) -> Any: ...

    def fetchall(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class CandidateDocument(BaseModel):
    """선별에 올리는 문서 한 건. 본문은 담지 않는다."""

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
    # 시간 단위. 슬롯 간격이 3.5시간 같은 반시간이라 정수가 아니다.
    window_hours: float
    detected: int
    assessed: int
    positive: int
    negative: int
    neutral: int
    backlog: int
    oldest_pending: AwareDatetime | None = None
    candidates: tuple[CandidateDocument, ...] = ()

    @property
    def is_empty(self) -> bool:
        return self.assessed == 0

    def by_id(self, document_id: int) -> CandidateDocument | None:
        return next((document for document in self.candidates if document.document_id == document_id), None)

    @property
    def allowed_ids(self) -> frozenset[int]:
        """모델이 고를 수 있는 id. 목록 밖의 값은 `picks.py`가 버린다."""
        return frozenset(document.document_id for document in self.candidates)


def window_hours_at(now: datetime) -> float:
    """직전 발송 슬롯부터 지금까지의 시간.

    24시간 고정 창은 발송이 하루 여러 번이 되면 같은 문서를 매번 다시 싣는다. 지난 발송
    이후 평가된 것만 실어야 한 문서가 한 번 나온다. 벽시계 차이가 아니라 슬롯 기준이라
    실행이 몇 분 밀려도 창이 이어진다(빈틈은 겹침 쪽으로 흡수된다).
    """
    local = now.astimezone(KST_TIMEZONE)
    slots = [local.replace(hour=hour, minute=minute, second=0, microsecond=0) for hour, minute in SEND_SLOTS_KST]
    passed = [slot for slot in slots if slot <= local]
    if len(passed) >= 2:
        previous = passed[-2]
    else:
        # 오늘 첫 슬롯이거나 그 전이다. 직전 발송은 어제 마지막(또는 그 앞) 슬롯이다.
        previous = slots[len(passed) - 2] - timedelta(days=1)
    return round((local - previous).total_seconds() / 3600, 1)


def collect_summary(
    connection: Connection,
    now: datetime,
    window_hours: float = WINDOW_HOURS,
    candidate_documents: int = CANDIDATE_DOCUMENTS,
) -> DocumentSummary:
    since = now - timedelta(hours=window_hours)
    with connection.cursor() as cursor:
        cursor.execute(BRIEFING_SUMMARY, (since,))
        counts = cursor.fetchone()
        cursor.execute(BRIEFING_CANDIDATES, (since, candidate_documents))
        candidates = cursor.fetchall()

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
        candidates=tuple(
            CandidateDocument(
                document_id=row[0],
                title=row[1],
                source_slug=row[2],
                direction=row[3],
                value_score=row[4],
                canonical_url=row[5],
                reason=row[6],
                tickers=tuple(row[7] or ()),
            )
            for row in candidates
        ),
    )


def render_blocks(
    summary: DocumentSummary,
    picks: Sequence[Pick] | None = None,
    error: str | None = None,
) -> list[dict[str, Any]]:
    local = summary.generated_at.astimezone(KST_TIMEZONE)
    rendered = [blocks.header(f"📰 문서 평가 브리핑 · {blocks.timestamp(local)}")]

    if summary.is_empty:
        rendered.append(
            blocks.section(f"최근 {summary.window_hours:g}시간 신규 평가 문서 없음 · 대기 {summary.backlog}건")
        )
        rendered.append(blocks.context(_as_of(summary)))
        return rendered

    rendered += blocks.table_section(
        f"최근 {summary.window_hours:g}시간",
        ("구분", "건수"),
        (
            ("신규 수집", f"{summary.detected:,}"),
            ("평가 완료", f"{summary.assessed:,}"),
            ("긍정", f"{summary.positive:,}"),
            ("부정", f"{summary.negative:,}"),
            ("중립", f"{summary.neutral:,}"),
            ("평가 대기", f"{summary.backlog:,}"),
        ),
    )
    rendered += _picked_sections(summary, picks) if picks is not None else _fallback_section(summary)
    if error:
        # 조용히 빠지지 않는다. 선별이 없는 리포트와 실패한 리포트는 구분돼야 한다.
        rendered.append(blocks.context([f"⚠️ 문서 선별 실패: {error}"]))
    rendered.append(blocks.context(_as_of(summary)))
    return rendered


def _picked_sections(summary: DocumentSummary, picks: Sequence[Pick]) -> list[dict[str, Any]]:
    """고른 것만 그린다. 한 건도 고르지 않았으면 그렇게 적는다."""
    reads = [pick for pick in picks if not pick.watch]
    watches = [pick for pick in picks if pick.watch]
    if not reads and not watches:
        return [blocks.section(f"오늘 따로 읽을 만한 문서 없음 · 후보 {len(summary.candidates)}건")]

    sections = []
    if reads:
        sections.append(blocks.section("*읽을 것*\n" + _pick_lines(summary, reads)))
    if watches:
        sections.append(blocks.section("*주의*\n" + _pick_lines(summary, watches)))
    return sections


def _fallback_section(summary: DocumentSummary) -> list[dict[str, Any]]:
    """선별을 못 했을 때. 예전처럼 점수 순서 상위 몇 건을 그린다."""
    head = summary.candidates[:FALLBACK_DOCUMENTS]
    if not head:
        return []
    return [blocks.section("*주요 문서(점수순)*\n" + "\n".join(_document_line(document) for document in head))]


def render_text(summary: DocumentSummary, picks: Sequence[Pick] | None = None) -> str:
    if summary.is_empty:
        return f"문서 평가 브리핑 · 신규 없음 · 대기 {summary.backlog}건"
    head = _head_title(summary, picks)
    return f"문서 평가 브리핑 · 평가 {summary.assessed}건 · {head}"


def _head_title(summary: DocumentSummary, picks: Sequence[Pick] | None) -> str:
    for pick in picks or ():
        document = summary.by_id(pick.document_id)
        if document:
            return document.title
    if picks is not None:
        return "읽을 문서 없음"
    return summary.candidates[0].title if summary.candidates else "주요 문서 없음"


def pick_input(summary: DocumentSummary) -> str:
    """선별에 줄 입력. 제목·이유·태그까지만 준다. 본문은 넣지 않는다."""
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
        "documents": [
            {
                "document_id": document.document_id,
                "title": document.title,
                "source": document.source_slug,
                "direction": document.direction,
                "value_score": document.value_score,
                "reason": document.reason,
                "tickers": list(document.tickers),
            }
            for document in summary.candidates
        ],
    }
    return json.dumps(payload, ensure_ascii=False)


def _pick_lines(summary: DocumentSummary, picks: Sequence[Pick]) -> str:
    lines = []
    for pick in picks:
        document = summary.by_id(pick.document_id)
        if document is None:
            # `picks.py`가 이미 걸렀지만, 렌더링이 목록에 없는 id로 죽지 않게 한다.
            continue
        lines.append(_picked_line(document, pick.why))
    return "\n".join(lines)


def _picked_line(document: CandidateDocument, why: str) -> str:
    mark = DIRECTION_MARKS.get(document.direction or "", "⚪")
    tags = f" `{'` `'.join(document.tickers)}`" if document.tickers else ""
    head = f"{mark} <{document.canonical_url}|{document.title}> — {document.source_slug}{tags}"
    return f"{head}\n_{why}_" if why else head


def _document_line(document: CandidateDocument) -> str:
    mark = DIRECTION_MARKS.get(document.direction or "", "⚪")
    tags = f" `{'` `'.join(document.tickers)}`" if document.tickers else ""
    return f"{mark} *{document.value_score}* <{document.canonical_url}|{document.title}> — {document.source_slug}{tags}"


def _as_of(summary: DocumentSummary) -> list[str]:
    lines = [f"평가 대기 {summary.backlog}건"]
    if summary.oldest_pending:
        waiting = summary.generated_at - summary.oldest_pending
        lines.append(f"최장 대기 {waiting.total_seconds() / 3600:.1f}시간")
    return lines
