"""세 추론 DAG가 XCom으로 넘기는 `ThesisRunResult`.

`build()` 셋이 같은 세 칸을 돌려주고 `notify_slack`·`narrate_followups`가 그것을 읽는다.
그 계약을 검사하는 테스트가 이 파일이 생기기 전에는 하나도 없었다.
"""

from datetime import date

import pytest
from pydantic import ValidationError

from modules import thesis_forecast, thesis_nxt_review, thesis_review
from modules.thesis_state import ThesisRunResult

BUILDERS = (thesis_forecast, thesis_review, thesis_nxt_review)


def test_the_three_slots_agree_on_the_result_shape():
    """셋이 같은 모델을 돌려주지 않으면 `notify_slack` 하나가 셋을 못 받는다."""
    assert {module.build.__annotations__["return"] for module in BUILDERS} == {ThesisRunResult}
    assert {module.SLOT for module in BUILDERS} == {"pre_open", "post_close", "post_nxt_close"}


def test_the_xcom_payload_keeps_the_wire_shape():
    """XCom에 실리는 것은 모델이 아니라 그 JSON이다. 날짜는 `YYYY-MM-DD` 문자열이다."""
    result = ThesisRunResult(run_date=date(2026, 8, 21), slot="pre_open", written=3)

    assert result.model_dump(mode="json") == {"run_date": "2026-08-21", "slot": "pre_open", "written": 3}


def test_reading_the_payload_back_gives_a_real_date():
    """읽는 쪽이 `date.fromisoformat`을 직접 부르지 않는다. 모델이 되돌린다."""
    restored = ThesisRunResult.model_validate({"run_date": "2026-08-21", "slot": "post_close", "written": 0})

    assert restored.run_date == date(2026, 8, 21)
    assert restored.slot == "post_close"


def test_a_broken_payload_fails_instead_of_reaching_slack():
    """예전에는 `built["run_date"]`가 KeyError로 죽는 자리가 Slack 발송 태스크 안이었다."""
    with pytest.raises(ValidationError):
        ThesisRunResult.model_validate({"slot": "pre_open", "written": 1})
