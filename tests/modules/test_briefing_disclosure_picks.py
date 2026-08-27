"""공시 강조 선별의 계약. 설계는 docs/briefing/disclosure-briefing.md 5절이다."""

import json

import pytest

from modules.briefing.disclosure_picks import DisclosurePicker
from modules.briefing.disclosures import MAX_REASON_CHARS, HighlightError

ALLOWED = frozenset({"20260827000123", "20260827000456"})


def response(**payload) -> str:
    return json.dumps(payload, ensure_ascii=False)


def test_it_keeps_only_the_receipt_numbers_from_the_candidate_list():
    raw = response(
        highlights=[
            {"rcept_no": "20260827000123", "reason": "실적이다"},
            {"rcept_no": "99999999999999", "reason": "지어낸 것이다"},
        ]
    )

    kept = DisclosurePicker.parse(raw, ALLOWED)

    assert [highlight.rcept_no for highlight in kept] == ["20260827000123"]


def test_all_receipt_numbers_outside_the_list_is_a_failure():
    """모델이 후보를 안 보고 답했다는 뜻이라 교정을 요청할 값어치가 있다."""
    raw = response(highlights=[{"rcept_no": "99999999999999", "reason": "x"}])

    with pytest.raises(HighlightError):
        DisclosurePicker.parse(raw, ALLOWED)


def test_highlighting_nothing_is_a_normal_answer():
    """정기 보고만 올라온 창이 그렇다. 억지로 채우지 않는 것이 정답이다."""
    assert DisclosurePicker.parse(response(highlights=[]), ALLOWED) == ()


def test_a_long_reason_is_trimmed_not_dropped():
    raw = response(highlights=[{"rcept_no": "20260827000123", "reason": "가" * (MAX_REASON_CHARS + 50)}])

    kept = DisclosurePicker.parse(raw, ALLOWED)

    assert len(kept) == 1
    assert len(kept[0].reason) == MAX_REASON_CHARS


def test_a_fenced_response_is_still_read():
    """스키마를 강제하지 못한 제공처가 코드 펜스를 붙여 온다."""
    raw = "```json\n" + response(highlights=[{"rcept_no": "20260827000456", "reason": "정정이다"}]) + "\n```"

    assert DisclosurePicker.parse(raw, ALLOWED)[0].rcept_no == "20260827000456"


def test_an_unusable_object_is_a_failure():
    with pytest.raises(HighlightError):
        DisclosurePicker.parse(response(highlights=[{"reason": "접수번호가 없다"}]), ALLOWED)


def test_the_prompt_carries_the_reason_limit():
    """상수를 고치면 프롬프트가 따라간다. 두 곳에 숫자를 적으면 반드시 어긋난다."""
    from modules.briefing import disclosure_picks

    assert str(MAX_REASON_CHARS) in disclosure_picks.SYSTEM_PROMPT


def test_the_prompt_carries_the_shared_number_style():
    """산문에 숫자가 들어가는 자리라 표기 규칙 한 벌을 함께 싣는다."""
    from modules import llm
    from modules.briefing import disclosure_picks

    assert llm.NUMBER_STYLE in disclosure_picks.SYSTEM_PROMPT


def test_the_prompt_forbids_investment_advice():
    from modules.briefing import disclosure_picks

    assert "투자 조언" in disclosure_picks.SYSTEM_PROMPT
