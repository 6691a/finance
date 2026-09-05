"""포착 Slack 블록을 만든다. **상태가 없는 순수 함수뿐이다.**

## 원인을 한 글자도 적지 않는다

장중 포착은 사실만 낸다. 마지막 줄이 "원인은 다음 영업일 아침부터 찾는다"인 것이 그
약속이다 — 그 시각에 우리가 가진 문서로는 원인이 안 나오고, 물으면 모델이 관계없는
같은 날 기사를 붙여 지어낸다.

## 못 본 시장을 0%로 적지 않는다

`available=False`인 시장은 "데이터 없음"으로 적는다. 빈 칸을 0으로 채우면 "안 움직였다"와
"못 봤다"가 같아 보이고, 니케이가 15~16분 지연이라 실제로 자주 일어난다.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from modules.shock.domain import (
    PEER_SPECS,
    REGION_LABELS,
    CauseAnswer,
    CauseInput,
    CauseKind,
    Direction,
    DocumentRow,
    PeerMove,
    PeerRegion,
    SearchRow,
    ShockEvent,
)
from modules.utility import KST_TIMEZONE

ARROWS = {Direction.DROP: "▼", Direction.SURGE: "▲"}
HEADS = {Direction.DROP: "⚡ 코스피 급락 포착", Direction.SURGE: "⚡ 코스피 급등 포착"}


def _kst(moment: datetime) -> str:
    return moment.astimezone(KST_TIMEZONE).strftime("%m/%d %H:%M")


def _hhmm(moment: datetime) -> str:
    return moment.astimezone(KST_TIMEZONE).strftime("%H:%M")


def _signed(value: Decimal) -> str:
    return f"{value:+.2f}%"


def _price(value: Decimal) -> str:
    return f"{value:,.2f}"


def render_text(event: ShockEvent) -> str:
    """알림과 검색 결과에 뜨는 대체 문구. 블록을 못 그리는 자리가 읽는다."""
    return f"{HEADS[event.direction]} {_kst(event.detected_at)} KST {_signed(event.move_pct)}"


def _peer_line(peer: PeerMove) -> str:
    spec = PEER_SPECS.get(peer.symbol)
    suffix = f"   ({spec.note})" if spec and spec.note else ""
    if not peer.available or peer.change_pct is None:
        return f"  {peer.label:<13} 데이터 없음{suffix}"
    return f"  {peer.label:<13} {_signed(peer.change_pct):>7}{suffix}"


def _peer_lines(peers: list[PeerMove] | tuple[PeerMove, ...]) -> str:
    """지역별로 묶어 적는다. **아시아와 미국 선물은 다른 질문에 답한다.**

    아시아 넷은 "한국만인가 아시아 전체인가"를, 미국 선물 둘은 "아시아만인가 글로벌인가"를
    가른다. 한 덩어리로 붙여 놓으면 읽는 사람이 그 층을 못 본다.
    """
    blocks = []
    for region in PeerRegion:
        rows = [peer for peer in peers if peer.region is region]
        if not rows:
            continue
        blocks.append(f"  [{REGION_LABELS[region]}]\n" + "\n".join(_peer_line(peer) for peer in rows))
    return "\n".join(blocks)


def moved_together(direction: Direction, peers: list[PeerMove] | tuple[PeerMove, ...], region: PeerRegion) -> int:
    """그 지역에서 같은 방향으로 움직인 시장 수. **값이 없는 시장은 안 센다.**

    `search.build_queries`도 이것을 쓴다 — "한국 일본 중국 증시 동시 급락" 질의를 만들지
    말지가 아시아 쪽 숫자로 정해진다.
    """
    count = 0
    for peer in peers:
        if peer.region is not region or not peer.available or peer.change_pct is None:
            continue
        if (direction is Direction.DROP and peer.change_pct < 0) or (
            direction is Direction.SURGE and peer.change_pct > 0
        ):
            count += 1
    return count


def _seen(peers: list[PeerMove] | tuple[PeerMove, ...], region: PeerRegion) -> int:
    return sum(1 for peer in peers if peer.region is region and peer.available and peer.change_pct is not None)


def _verdict(event: ShockEvent, peers: list[PeerMove]) -> str:
    """지역마다 몇이 같이 움직였는지 센다. **판정하지 않고 세기만 한다.**

    "동조 점수" 같은 값을 만들지 않는다 — 눈금을 또 정해야 하고 읽는 쪽은 결국 원값을 본다.

    **두 지역이 다른 질문에 답한다.** 아시아가 같이 움직였고 미국 선물은 안 움직였으면
    아시아 재료이고, 셋 다 움직였으면 글로벌이다. 그 구분이 이 문장의 값어치다.
    """
    word = "빠졌다" if event.direction is Direction.DROP else "올랐다"
    asia_seen, us_seen = _seen(peers, PeerRegion.ASIA), _seen(peers, PeerRegion.US)
    if not asia_seen and not us_seen:
        return "다른 시장의 봉이 아직 없다. 한국만의 재료인지 가릴 수 없다."

    asia = moved_together(event.direction, peers, PeerRegion.ASIA)
    us = moved_together(event.direction, peers, PeerRegion.US)
    parts = []
    if asia_seen:
        parts.append(f"아시아 {asia_seen} 중 {asia}")
    if us_seen:
        parts.append(f"미국 선물 {us_seen} 중 {us}")
    counted = ", ".join(parts) + f"가 같이 {word}."

    if us_seen and us == us_seen and asia_seen and asia == asia_seen:
        return f"{counted} 글로벌 재료일 수 있다."
    if asia_seen and asia and not us:
        return f"{counted} 아시아 재료일 수 있다."
    if not asia and not us:
        return f"{counted} 국내 재료일 수 있다."
    return f"{counted} 한국만의 재료가 아닐 수 있다."


def render_blocks(event: ShockEvent, peers: list[PeerMove]) -> list[dict[str, Any]]:
    """포착 메시지 한 장."""
    window = f"{_kst(event.window_start)}~{_hhmm(event.window_end)} KST"
    change = f"   창 등락 {_signed(event.window_change_pct)}" if event.window_change_pct is not None else ""
    headline = (
        f"코스피   {_signed(event.move_pct)}   "
        f"{_price(event.extreme_price)} ({_hhmm(event.extreme_at)}) → "
        f"{_price(event.trigger_price)} ({_hhmm(event.detected_at)}){change}"
    )
    lines = _peer_lines(peers)

    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{HEADS[event.direction]} · {window}", "emoji": True},
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"```{headline}```"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*같은 창*\n```{lines}```"}},
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"{ARROWS[event.direction]} {_verdict(event, peers)} "
                        f"원인은 다음 영업일 아침부터 찾는다. · 봉 {event.bar_count}개 · "
                        f"임계 ±{event.threshold_pct:.1f}%"
                    ),
                }
            ],
        },
    ]


# --- 원인 분석 -----------------------------------------------------------------

CAUSE_KIND_LABELS = {
    CauseKind.RUMOR: "루머",
    CauseKind.CONFIRMED: "사실 확인",
    CauseKind.UNCLEAR: "방아쇠 불명",
}


def _document_block(rows: tuple[DocumentRow, ...]) -> str:
    if not rows:
        return "(없다)"
    lines = []
    for row in rows:
        facts = " / ".join(row.new_facts) if row.new_facts else "-"
        score = row.value_score if row.value_score is not None else "-"
        lines.append(
            f"[{row.id}] {_kst(row.published_at)} · {row.source_slug} · 점수 {score}\n"
            f"  제목: {row.title}\n"
            f"  평가: {row.reason or '-'}\n"
            f"  사실: {facts}"
        )
    return "\n\n".join(lines)


def _search_block(rows: tuple[SearchRow, ...]) -> str:
    if not rows:
        return "(검색 결과가 없다)"
    lines = []
    for row in rows:
        stamp = _kst(row.published_at) if row.published_at else "발행 시각 없음"
        lines.append(
            f"({row.index}) {stamp} · {row.publisher}\n"
            f"  제목: {row.title}\n"
            f"  발췌: {row.snippet}\n"
            f"  URL: {row.url}"
        )
    return "\n\n".join(lines)


def render_cause_prompt_blocks(payload: CauseInput) -> dict[str, str]:
    """프롬프트 자리표시자에 들어갈 문자열들.

    **시각은 전부 KST 표기로 준다.** UTC ISO를 그대로 실으면 모델이 "그날"을 하루 어긋나게
    읽는다(저장소 규칙).
    """
    peers = _peer_lines(payload.peers) or "  (비교할 시장이 없다)"
    return {
        "detected_kst": _kst(payload.detected_at),
        "detected_hhmm": _hhmm(payload.detected_at),
        "extreme_kst": _hhmm(payload.extreme_at),
        "extreme_price": _price(payload.extreme_price),
        "trigger_price": _price(payload.trigger_price),
        "window_change_pct": (f"{payload.window_change_pct}" if payload.window_change_pct is not None else "모름"),
        "peers": peers,
        "as_of_kst": _kst(payload.as_of_at),
        "deadline": payload.deadline.isoformat() if payload.deadline else "미정",
        "documents": _document_block(payload.documents),
        "search_hits": _search_block(payload.search_hits),
    }


def render_cause_text(payload: CauseInput, answer: CauseAnswer) -> str:
    """알림 대체 문구."""
    head = "급락" if payload.direction is Direction.DROP else "급등"
    verdict = "원인 확인" if answer.found else "원인 못 찾음"
    return f"🔎 {_kst(payload.detected_at)} 코스피 {head}의 원인 — {verdict}"


def render_cause_blocks(
    payload: CauseInput,
    answer: CauseAnswer,
    *,
    cited_search: tuple[SearchRow, ...] = (),
    weak: bool = False,
) -> list[dict[str, Any]]:
    """원인을 찾았을 때의 메시지.

    **근거를 링크까지 싣는다.** 자동으로 못 잡는 것을 사람이 잡는 유일한 통로다 —
    한 번 눌러 "이 기사가 정말 그 얘긴가"를 볼 수 있으면 틀린 원인이 하루 안에 걸린다.
    """
    head = "급락" if payload.direction is Direction.DROP else "급등"
    title = f"🔎 {_kst(payload.detected_at)} 코스피 {head}의 원인 · {payload.attempt}차 시도"
    if weak:
        title = f"⚠ 근거 약한 원인 · {title}"

    by_id = {document.id: document for document in payload.documents}
    lines = []
    for doc_id in answer.document_ids:
        document = by_id.get(doc_id)
        if document is not None:
            lines.append(f"  [{doc_id}] {_kst(document.published_at)} · {document.source_slug} · {document.title}")
    for row in cited_search:
        stamp = _kst(row.published_at) if row.published_at else "발행 시각 없음"
        lines.append(f"  <{row.url}|{row.title}> — {stamp} · {row.publisher} (검색)")

    body = (
        f"*{title}*\n\n{answer.cause_text}\n\n"
        f"*판정:* {CAUSE_KIND_LABELS.get(answer.cause_kind, answer.cause_kind)}"
    )
    blocks: list[dict[str, Any]] = [{"type": "section", "text": {"type": "mrkdwn", "text": body}}]
    if lines:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*근거*\n" + "\n".join(lines)}})
    return blocks


def render_unknown_blocks(
    *,
    detected_at: datetime,
    direction: Direction,
    attempts: int,
    deadline: str,
) -> list[dict[str, Any]]:
    """기한을 다 쓰고 못 찾았을 때. **닫힐 때도 보낸다.**

    안 보내면 포착 메시지만 남아서 읽는 사람이 "원인이 아직 안 왔나"를 영원히 기다린다.
    """
    head = "급락" if direction is Direction.DROP else "급등"
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"*🔎 {_kst(detected_at)} 코스피 {head}의 원인 — 못 찾았다*\n\n"
                    f"{deadline}까지 {attempts}번 봤지만 이 {head}을 설명하는 것이 "
                    f"우리 문서에도 검색에도 없었다."
                ),
            },
        }
    ]
