"""저장된 추론을 Slack 블록으로.

상태가 없다. `ThesisStore`가 읽어 온 모델을 받아 표와 블록을 만드는 순수 변환이라 함수로
둔다 — 클래스로 묶으면 전부 `@staticmethod`가 된다(프로젝트 규칙).
"""

"""시장 추론(thesis)을 만들고, 저장하고, 채점한다.

**맞고 틀림이 목적이 아니다.** "어떤 정보를 근거로 어떤 결론을 냈다"가 기록으로 남는 것이
목적이다. 채점은 그 기록 위에 나중에 얹히고, 틀린 판단도 고치지 않는다.

## 근거는 고정 풀이 아니라 모델이 조회한다

프롬프트에는 **관측 상태만** 준다("코스피 +1.61%", "SK하이닉스 전일 -2.1%"). 관측 상태는
전부 SQL이 계산한다. 왜인지 알아내는 데 필요한 정보는 모델이 `ThesisToolbox`의 읽기 전용 툴을
호출해 스스로 가져온다 — 어떤 것을 얼마나 볼지는 모델이 정한다.

**모델이 실제로 인용한 근거만 저장한다.** 툴이 돌려준 항목에는 전부 `ref`가 붙어 있고,
답변의 `evidence_refs`는 그 레지스트리로 검증한다. 목록 밖 ref는 버린다. 이것이 모델이 근거를
지어내지 못하게 막는 유일한 장치다.

## 조사와 답변을 나눈다

`modules/llm.py`의 원칙 그대로다. 조사 단계는 툴만 바인딩하고, 답변 단계는 툴을 빼고
`response_format`을 강제한다. 한 요청에 둘을 섞지 않는다 — `llm.invoke`가 그것을 막는다.

## 기준 시각은 벽시계가 아니다

**모든 조회의 끝은 슬롯이 정한 `as_of_at`이다.** 오후에 장전 슬롯을 다시 돌려도 장중 정보로
아침 예측을 덮지 않는다. 이것은 event-time cutoff다 — 현재 DB에서 확인 가능한 범위에서
`as_of_at` 이후 감지·평가·갱신된 행을 뺀다. 과거 시점을 완전히 복원하지는 못한다
(`document`는 본문·평가를 같은 행에 덮어쓰고 버전 이력을 두지 않는다).

## 첫 성공본은 불변이다

같은 (날짜, 슬롯)에 추론 행이 이미 있으면 LLM을 다시 부르지 않는다. LLM은 재호출마다 답이
달라서 덮어쓰면 최초 판단이 사라진다. `existing_theses`가 먼저 보고, 없을 때만 Builder를 돈다.

## 채점에 LLM이 없다

수식이 SQL이 아니라 파이썬에 있는 이유는 경계값을 DB 없이 테스트하기 위해서다(테스트에서
실 DB를 쓰지 않는 프로젝트 규칙). `select_session_return.sql`이 등락률을 주고
`update_outcome.sql`은 여기서 나온 값 넷을 쓰기만 한다.

설계는 `docs/analysis/market-thesis/1-storage.md`와 `docs/analysis/market-thesis/2-agent.md`에 있다.
"""

import logging
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from modules.thesis_domain import (
    SLOT_LABELS,
    ThesisVerdict,
)
from modules.thesis_state import (
    INTRADAY_SLOTS,
    RunSlot,
)
from modules.thesis_store import (
    StoredEvidence,
    StoredThesis,
)

logger = logging.getLogger(__name__)

# 근거 줄에 그릴 개수. 세 개를 넘으면 한 줄이 길어져 읽히지 않는다.
SLACK_EVIDENCE_LIMIT = 3

# 최고 확률에서 이만큼 안에 붙은 방향은 결론에 함께 보인다. 하락 41%·횡보 38%처럼 갈리는
# 것을 하나로 접으면 모델이 고르지 못한 것을 우리가 대신 골라 주는 셈이다.
#
# **0.05는 실측이 아니라 시작값이다.** 두 방향이 매번 함께 나오면 좁히고, 한 번도 함께
# 나오지 않으면 넓힌다. 프롬프트 캘리브레이션(`PROMPT_VERSION` 5)이 확률을 벌려 놓기
# 때문에 그 뒤 분포를 보고 정한다.
VERDICT_TIE_GAP = Decimal("0.05")

# 되돌아보기 섹션을 그릴 지평. T+1·T+3까지 매일 보내면 하루 세 덩이가 더 붙어 원래 알림이
# 묻힌다. T+5가 해설이 가장 굳은 시점이기도 하다.
SLACK_REVIEW_HORIZON = 5

# 헤더는 이모지 + 라벨이다. **장중 넷은 이모지가 같고 시각으로 갈린다** — 하루 다섯 건이
# 같은 채널에 쌓이므로 "언제 기준인가"가 값의 절반이다(차트·표 표기 규칙과 같은 이유).
# 라벨의 원본은 `thesis_domain.SLOT_LABELS`라 스케줄을 옮기면 여기도 따라온다.
SLOT_HEADERS = {
    RunSlot.PRE_OPEN: f"🔮 {SLOT_LABELS[RunSlot.PRE_OPEN]}",
    **{slot: f"⏱ {SLOT_LABELS[slot]}" for slot in INTRADAY_SLOTS},
    RunSlot.POST_CLOSE: f"🔎 {SLOT_LABELS[RunSlot.POST_CLOSE]}",
    RunSlot.POST_NXT_CLOSE: f"🌙 {SLOT_LABELS[RunSlot.POST_NXT_CLOSE]}",
}

DIRECTION_MARKS = {"up": "▲", "down": "▼", "flat": "–"}
DIRECTION_NAMES = {"up": "상승", "down": "하락", "flat": "횡보"}

# 판정을 영문 enum 값 그대로 보이면 읽는 사람이 매번 해석해야 한다. 이모지가 앞에서 갈라 준다.
VERDICT_LABELS = {
    ThesisVerdict.SUPPORTED: "✅ 이유 지지됨",
    ThesisVerdict.CONTRADICTED: "❌ 이유 반박됨",
    ThesisVerdict.UNRESOLVED: "❔ 판단 보류",
}


def _verdicts(thesis: StoredThesis) -> tuple[tuple[str, Decimal], ...]:
    """보일 방향과 확률. 최고 확률에서 `VERDICT_TIE_GAP` 안에 붙은 것을 내림차순으로 준다.

    하나만 나오는 것이 정상이지만 하락 41%·횡보 38%처럼 붙어 있으면 둘 다 낸다. 그때
    앞의 것만 보이면 모델이 고르지 못한 것을 우리가 대신 골라 준 셈이 된다.

    동률이면 up · down · flat 순이다(`sorted`가 안정 정렬이라 입력 순서를 지킨다).
    """
    ranked = sorted(
        (("up", thesis.prob_up), ("down", thesis.prob_down), ("flat", thesis.prob_flat)),
        key=lambda pair: pair[1],
        reverse=True,
    )
    top = ranked[0][1]
    return tuple(pair for pair in ranked if top - pair[1] <= VERDICT_TIE_GAP)


def _evidence_lines(items: Sequence[StoredEvidence], directions: Sequence[str]) -> str:
    """근거 줄. `context` 블록에 들어가 본문보다 작게 그려진다.

    **결론 방향의 근거만 그린다.** 세 방향 확률을 다 보이던 때는 근거도 방향과 무관하게
    상위 세 개였지만, 결론만 보이는 지금 반대 방향 근거를 함께 두면 "그래서 왜 이
    결론인가"가 흐려진다. 반대편 근거는 DB에 그대로 남는다.

    맞는 근거가 없으면 방향을 가리지 않고 상위 몇 개를 그린다. `direction`이 비어 있는
    근거(모델이 `claims`에 안 담은 것)도 그때 나온다 — 인용한 것이 있는데 아무 것도 안
    보이는 편이 더 나쁘다.

    URL이 있는 것만 링크로 만든다 — 매크로 변화는 링크할 곳이 없다.
    """
    if not items:
        # 억지 인용보다 낫다는 판단의 결과라 그렇게 적는다.
        return "📎 근거 없음 — 관측 상태만으로 추론"
    matched = [item for item in items if item.direction in directions]
    # 방향이 둘 이상이거나 폴백이면 어느 쪽 근거인지 밝혀야 읽힌다.
    label_direction = len(directions) > 1 or not matched
    lines = ["📎 *판단 근거*"]
    for item in (matched or items)[:SLACK_EVIDENCE_LIMIT]:
        title = f"<{item.evidence_url}|{item.evidence_title}>" if item.evidence_url else item.evidence_title
        mark = f" ({DIRECTION_NAMES[item.direction]})" if label_direction and item.direction else ""
        lines.append(f"• {title}{mark}")
        if item.mechanism:
            lines.append(f"    {item.mechanism}")
    return "\n".join(lines)


def _thesis_section(thesis: StoredThesis, verdicts: Sequence[tuple[str, Decimal]]) -> str:
    """추론 하나. 결론 줄과 그 방향의 이유다.

    세 확률·세 이유를 늘 늘어놓던 것을 2026-08-25에 이 형태로 바꿨다. 확률이 균등 근처에
    몰려 있어 세 값을 나란히 두면 어느 것이 판단인지 읽는 사람이 매번 골라야 했다.
    보이지 않는 확률과 이유도 `thesis` 테이블에 그대로 남고 채점은 셋을 다 쓴다.
    """
    reasonings = {
        "up": thesis.up_reasoning,
        "down": thesis.down_reasoning,
        "flat": thesis.flat_reasoning,
    }
    verdict_line = "   ".join(
        f"*{DIRECTION_MARKS[direction]} {DIRECTION_NAMES[direction]} {probability:.0%}*"
        for direction, probability in verdicts
    )
    # 방향이 하나면 바로 위 줄이 이미 방향을 말했다. 둘 이상일 때만 이유마다 표시를 단다.
    lines = [f"*{thesis.label}*", verdict_line]
    for direction, _ in verdicts:
        prefix = f"*{DIRECTION_MARKS[direction]}* " if len(verdicts) > 1 else ""
        lines.append(f"> {prefix}{reasonings[direction]}")
    return "\n".join(lines)


def render_blocks(
    run_slot: RunSlot,
    run_date: date,
    theses: Sequence[StoredThesis],
    evidence: dict[int, tuple[StoredEvidence, ...]],
) -> list[dict[str, Any]]:
    """Slack 블록. 추론이 0건이면 그 사실을 한 줄로 알린다.

    대상마다 `section`(결론·이유)과 `context`(근거) 둘을 낸다. 근거를 본문에 두면 이유
    문장과 같은 무게로 읽혀 어느 것이 판단이고 어느 것이 출처인지 흐려진다.

    **채점과 사후 해설은 여기 싣지 않는다**(2026-08-21 결정). 읽는 사람이 다르다 — 이 메시지는
    오늘 시장을 보는 사람이 읽고, "우리 추론이 잘 맞고 있나"는 운영자가 본다. 지표는
    `slack_ops_briefing`이 낸다. 한 메시지에 섞으면 매일 아침 자기 감사 보고가 딸려 온다.
    """
    from modules.briefing import blocks as block

    weekday = block.WEEKDAY_NAMES[run_date.weekday()]
    built: list[dict[str, Any]] = [block.header(f"{SLOT_HEADERS[run_slot]} · {run_date:%m/%d}({weekday})")]
    if not theses:
        built.append(block.section("_이번 슬롯에 남은 추론이 없다._"))
        return built
    for thesis in theses:
        verdicts = _verdicts(thesis)
        built.append(block.section(_thesis_section(thesis, verdicts)))
        directions = [direction for direction, _ in verdicts]
        built.append(block.context([_evidence_lines(evidence.get(thesis.id, ()), directions)]))
    return built


def render_text(run_slot: RunSlot, run_date: date, theses: Sequence[StoredThesis]) -> str:
    """블록을 못 그리는 자리(알림, 검색)에 뜨는 대체 문구. 항상 채운다."""
    if not theses:
        return f"{SLOT_HEADERS[run_slot]} {run_date:%m/%d} — 추론 결과 없음"
    names = " · ".join(thesis.label for thesis in theses)
    return f"{SLOT_HEADERS[run_slot]} {run_date:%m/%d} — {names}"
