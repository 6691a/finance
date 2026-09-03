"""Slack 블록을 만든다. **상태가 없는 순수 함수뿐이다.**

Slack은 프론트엔드가 없는 출력이라 "어느 시장의 언제 값인가"를 여기서 밝히는 것이 유일한
단서다(저장소 규칙). 그래서 모든 숫자 줄에 기준가와 그 시각이 붙는다.

## 약한 답은 티가 나야 한다

근거 0건으로 저장된 전망은 머리표가 붙는다. 정상 답과 같아 보이면 매일 읽는 사람이 이상한
날을 못 고른다.

## 이유는 셋만 보인다

`SLACK_REASON_LIMIT`은 **표시 손잡이이지 저장 손잡이가 아니다.** 이유 개수에는 상한이 없고
전부 DB에 있다. 여기서 자르는 것은 메시지가 길어 안 읽히기 때문이다.
"""

from datetime import date
from decimal import Decimal
from typing import Any

from modules.kospi.domain import (
    INDEX_LABEL,
    SLACK_REASON_LIMIT,
    SLOT_LABELS,
    Direction,
    RunSlot,
)

# Slack 블록 상한은 50이다. 하루 셋에 관찰까지 실어도 한참 남지만, 이유가 수십 개 오는 날을
# 위해 자르는 자리를 둔다.
MAX_BLOCKS = 45

ARROWS = {Direction.UP: "▲", Direction.DOWN: "▼"}


def render_text(built: dict[str, Any]) -> str:
    """알림과 검색 결과에 뜨는 대체 문구. 블록을 못 그리는 자리가 읽는다."""
    kind = built.get("kind", "forecast")
    run_date = built.get("run_date", "")
    if kind == "review":
        return f"{INDEX_LABEL} {run_date} 장후 관찰"
    slot = built.get("slot", "")
    label = SLOT_LABELS.get(_slot_or_none(slot), slot)
    return f"{INDEX_LABEL} {run_date} {label} 전망"


def render_blocks(built: dict[str, Any]) -> list[dict[str, Any]]:
    """`common.notify_slack`이 부르는 유일한 진입점.

    `built`는 XCom을 지난 dict이고, 발송 태스크가 그것으로 DB를 다시 읽어 여기 넘긴다 —
    전망 내용을 XCom에 싣지 않는 것이 그 이유다.
    """
    if built.get("kind") == "review":
        return _review_blocks(built)[:MAX_BLOCKS]
    return _forecast_blocks(built)[:MAX_BLOCKS]


def _forecast_blocks(built: dict[str, Any]) -> list[dict[str, Any]]:
    slot = _slot_or_none(built.get("slot"))
    label = SLOT_LABELS.get(slot, str(built.get("slot", "")))
    weak = bool(built.get("weak"))
    heading = f"⚠ 근거 없는 답 · {INDEX_LABEL} {label} 전망" if weak else f"{INDEX_LABEL} {label} 전망"

    blocks: list[dict[str, Any]] = [
        {"type": "header", "text": {"type": "plain_text", "text": heading, "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": _headline(built)}},
    ]
    reasons = list(built.get("reasons") or ())
    if reasons:
        lines = [_reason_line(item) for item in reasons[:SLACK_REASON_LIMIT]]
        if len(reasons) > SLACK_REASON_LIMIT:
            lines.append(f"_외 {len(reasons) - SLACK_REASON_LIMIT}건은 기록에 있다_")
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(lines)}})
    else:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "_검증을 통과한 근거가 없다. 이 전망은 참고만 한다._"},
            }
        )
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _footer(built)}]})
    return blocks


def _review_blocks(built: dict[str, Any]) -> list[dict[str, Any]]:
    run_date = built.get("run_date", "")
    change = _decimal(built.get("change_pct"))
    close = _decimal(built.get("close"))
    arrow = "▲" if change is not None and change > 0 else "▼" if change is not None and change < 0 else "―"

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"{INDEX_LABEL} {run_date} 장후 관찰", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{arrow} {_signed(change)}%* 종가 {_price(close)} · KRX 정규장 확정 ({run_date})",
            },
        },
    ]

    grades = list(built.get("grades") or ())
    if grades:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "*채점*\n" + "\n".join(_grade_line(item) for item in grades)}}
        )

    observations = list(built.get("observations") or ())
    if observations:
        lines = [_observation_line(item) for item in observations]
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "*오늘 무엇이 움직였나*\n" + "\n".join(lines)}})
    else:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "_관찰 없음 — 조용한 날이다._"}})

    memories = list(built.get("new_memories") or ())
    if memories:
        blocks.append(
            {"type": "section", "text": {"type": "mrkdwn", "text": "*새 메모*\n" + "\n".join(f"• {item}" for item in memories)}}
        )
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": _memory_footer(built)}]})
    return blocks


def _headline(built: dict[str, Any]) -> str:
    """방향·크기·폭과 **기준가**. 기준가가 없으면 숫자가 무엇 대비인지 알 수 없다."""
    direction = _direction_or_none(built.get("direction"))
    expected = _decimal(built.get("expected_change_pct"))
    band = _decimal(built.get("band_pct"))
    arrow = ARROWS.get(direction, "―") if direction else "―"
    base = _price(_decimal(built.get("base_price")))
    base_at = built.get("base_at_kst") or ""
    note = built.get("base_note") or ""
    so_far = _decimal(built.get("so_far_pct"))

    head = f"*{arrow} {_signed(expected)}% ± {_plain(band)}%p*"
    basis = f"기준 {base} ({base_at})"
    if note:
        basis = f"{basis} · {note}"
    if so_far is not None:
        basis = f"{basis} · 지금까지 {_signed(so_far)}%"
    return f"{head}\n{basis}"


def _reason_line(item: dict[str, Any]) -> str:
    direction = _direction_or_none(item.get("direction"))
    arrow = ARROWS.get(direction, "·") if direction else "·"
    tag = item.get("factor") or (f"메모 {item['memory_id']}" if item.get("memory_id") else None)
    tag = tag or (f"{SLOT_LABELS.get(_slot_or_none(item.get('slot_ref')), '앞 슬롯')} 이어받음" if item.get("slot_ref") else None)
    prefix = f"*{tag}* " if tag else ""
    return f"{arrow} {prefix}{item.get('statement', '')}"


def _grade_line(item: dict[str, Any]) -> str:
    slot = SLOT_LABELS.get(_slot_or_none(item.get("slot")), str(item.get("slot", "")))
    direction = _direction_or_none(item.get("direction"))
    arrow = ARROWS.get(direction, "―") if direction else "―"
    expected = _decimal(item.get("expected_change_pct"))
    band = _decimal(item.get("band_pct"))
    actual = _decimal(item.get("actual_change_pct"))
    if actual is None:
        return f"• {slot} {arrow} {_signed(expected)}% ± {_plain(band)}%p — _채점 대기_"
    hit = "○" if item.get("hit") else "✕"
    within = "○" if item.get("within_band") else "✕"
    return f"• {slot} {arrow} {_signed(expected)}% ± {_plain(band)}%p → 실제 {_signed(actual)}% · 방향 {hit} 폭 {within}"


def _observation_line(item: dict[str, Any]) -> str:
    strength = int(item.get("strength") or 1)
    sign = "같은 방향" if item.get("sign") == "same" else "반대 방향"
    marks = "●" * strength
    return f"• *{item.get('label') or item.get('factor')}* {marks} {sign} — {item.get('note', '')}"


def _footer(built: dict[str, Any]) -> str:
    parts = [f"판 {built.get('prompt_version', '?')}", str(built.get("llm_model") or "")]
    rejected = int(built.get("rejected_reasons") or 0)
    if rejected:
        parts.append(f"버린 근거 {rejected}건")
    return " · ".join(part for part in parts if part)


def _memory_footer(built: dict[str, Any]) -> str:
    written = int(built.get("memories_written") or 0)
    kept = int(built.get("memories_kept") or 0)
    dropped = int(built.get("memories_dropped") or 0)
    expired = int(built.get("memories_expired") or 0)
    tail = f"메모 +{written} · 유지 {kept} · 지움 {dropped + expired}"
    if expired:
        tail = f"{tail}(만료 {expired})"
    return f"{tail} · 판 {built.get('prompt_version', '?')}"


def _signed(value: Decimal | None) -> str:
    if value is None:
        return "―"
    return f"{value:+.2f}"


def _plain(value: Decimal | None) -> str:
    return "―" if value is None else f"{value:.2f}"


def _price(value: Decimal | None) -> str:
    return "―" if value is None else f"{value:,.2f}"


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except Exception:  # noqa: BLE001 - 표시 층이라 값 하나가 메시지를 죽이지 않게 한다
        return None


def _direction_or_none(value: Any) -> Direction | None:
    try:
        return Direction(str(value))
    except ValueError:
        return None


def _slot_or_none(value: Any) -> RunSlot | None:
    try:
        return RunSlot(str(value))
    except ValueError:
        return None


def forecast_payload(row: Any, *, kind: str = "forecast") -> dict[str, Any]:
    """저장된 전망 하나를 Slack이 읽는 dict로. `render_blocks`의 입력을 만드는 자리다."""
    return {
        "kind": kind,
        "run_date": row.run_date.isoformat() if isinstance(row.run_date, date) else str(row.run_date),
        "slot": row.slot.value,
        "direction": row.direction.value,
        "expected_change_pct": str(row.expected_change_pct),
        "band_pct": str(row.band_pct),
        "base_price": str(row.base_price),
        "base_at_kst": "",
        "base_note": "",
        "so_far_pct": None if row.so_far_pct is None else str(row.so_far_pct),
        "reasons": list(row.reasons),
        "weak": row.weak,
        "rejected_reasons": row.rejected_reasons,
        "prompt_version": row.prompt_version,
        "llm_model": row.llm_model,
    }
