import json

import pytest

from modules.analysts import (
    CATEGORIES,
    MAX_TOOL_CALLS,
    AnalysisError,
    AnalystReport,
    analyst_messages,
    parse_analyst_report,
    parse_market_report,
    synthesis_messages,
    unsupported_numbers,
)
from modules.tools import TOOLS, tool_specs

BRIEF = "대상 기간: 2026-08-08 ~ 2026-08-14 (KST)"

RATES = AnalystReport.model_validate(
    {
        "observations": [
            {
                "statement": "미국 10년물이 한 주 동안 7bp 내렸다.",
                "series": ["fred:DGS10"],
                "numbers": [{"name": "change_bp", "value": -7}, {"name": "last", "value": 4.63}],
            }
        ],
        "summary": "미국 장기금리가 소폭 하락했다.",
    }
)

FX = AnalystReport.model_validate(
    {
        "observations": [
            {
                "statement": "원/달러가 20일 상관 0.06으로 반도체 지수와 거의 무관했다.",
                "series": ["yahoo:USDKRW", "yahoo:SOX"],
                "numbers": [{"name": "correlation", "value": 0.06}, {"name": "observations", "value": 250}],
            }
        ],
        "summary": "달러 방향이 뚜렷하지 않다.",
    }
)

REPORTS = [("rates", RATES), ("fx", FX)]

MARKET_REPORT = """{"headline": "미국 장기금리 하락, 환율은 방향성 없음",
 "body": "미국 10년물이 7bp 내려 4.63이다. 원/달러와 반도체 지수의 상관은 250일 기준 0.06으로 낮다.",
 "claims": [{"statement": "향후 5거래일 fred:DGS10 변화폭은 음수 쪽", "horizon_days": 5, "confidence": "low"}],
 "unresolved": ["수급 관찰이 없어 외국인 방향을 판단하지 못했다"]}"""


def test_every_category_only_asks_for_tools_that_exist():
    # 카테고리에 오타가 나면 그 분석가는 툴 없이 돌게 된다. 배포 전에 걸린다.
    for category in CATEGORIES.values():
        assert set(category.tools) <= set(TOOLS), category.name
        # tool_specs가 실제로 만들어지는지까지 확인한다.
        assert len(tool_specs(category.tools)) == len(category.tools)


def test_every_tool_is_reachable_by_at_least_one_category():
    # 아무 분석가도 못 보는 툴은 죽은 코드다.
    reachable = {name for category in CATEGORIES.values() for name in category.tools}
    assert reachable == set(TOOLS)


def test_categories_cover_what_we_actually_collect():
    # 금리·환율·위험자산·수급·기사. 하나가 빠지면 그 데이터는 리포트에 못 들어간다.
    assert set(CATEGORIES) == {"rates", "fx", "risk", "flow", "news"}


def test_the_rate_analysts_can_subtract():
    """계산할 도구가 없으면 분석가는 값을 나열하고 눈으로 비교한다.

    실측(grok-4)에서 6개국 전 만기의 시작값·끝값을 관찰에 그대로 옮겨 적고 "높아졌다"로
    끝냈다. 곡선 기울기와 나라 사이 벌어짐이 담당 영역인데 뺄셈 도구가 없었다.
    """
    for name in ("rates", "fx", "risk"):
        assert "series_change" in CATEGORIES[name].tools, name
        assert "series_spread" in CATEGORIES[name].tools, name


def test_the_news_analyst_cannot_read_price_series():
    # 카테고리를 나눈 뜻이 여기 있다. 전부 보여 주면 각자 남의 영역을 뒤진다.
    assert CATEGORIES["news"].tools == ("search_documents",)


def test_analyst_prompt_carries_its_focus_and_the_call_budget():
    category = CATEGORIES["rates"]

    messages = analyst_messages(category, BRIEF)

    system = messages[0]["content"]
    assert category.label in system
    assert "변화폭으로 읽는다" in system
    assert str(MAX_TOOL_CALLS) in system
    assert messages[1]["content"] == BRIEF


def test_analyst_prompt_forbids_conclusions():
    """분석가가 각자 결론을 내면 종합 단계가 데이터가 아니라 문장을 중재하게 된다."""
    system = analyst_messages(CATEGORIES["fx"], BRIEF)[0]["content"]

    assert "결론을 내지 않는다" in system
    assert "관찰만 남긴다" in system


def test_analyst_prompt_does_not_carry_data():
    # 데이터는 툴로 가져온다. 프롬프트에 미리 넣으면 툴을 둔 뜻이 없다.
    system, user = analyst_messages(CATEGORIES["risk"], BRIEF)
    assert len(user["content"]) < 200
    assert "4.63" not in system


def test_synthesis_gets_observations_and_not_raw_rows():
    messages = synthesis_messages(BRIEF, REPORTS)

    user = messages[1]["content"]
    assert "금리·채권 분석가" in user
    assert "환율·달러 분석가" in user
    assert "7bp 내렸다" in user
    # 종합 단계는 원자료를 다시 읽지 않는다. 툴 응답 형태가 새어 들어오면 안 된다.
    assert "business_date" not in user


def test_synthesis_prompt_forbids_numbers_that_were_not_given():
    system = synthesis_messages(BRIEF, REPORTS)[0]["content"]

    assert "프롬프트에 없는 숫자를 본문에 쓰지 않는다" in system
    assert "확인할 수 있는 형태" in system


def test_the_brief_period_does_not_cap_the_investigation_window():
    """7거래일짜리 상관은 잡음이다. 대상 기간과 조사 창을 분리해 준다."""
    system = analyst_messages(CATEGORIES["rates"], BRIEF)[0]["content"]

    assert "조사할 구간의 상한이 아니다" in system


def test_parse_analyst_report_accepts_a_code_fence():
    report = parse_analyst_report(f"```json\n{json.dumps(RATES.model_dump(), ensure_ascii=False)}\n```")

    assert [number.value for number in report.observations[0].numbers] == [-7, 4.63]


def test_parse_rejects_a_reply_without_json():
    with pytest.raises(AnalysisError, match="did not return a JSON object"):
        parse_analyst_report("판단할 수 없습니다.")


def test_parse_market_report_rejects_an_unknown_confidence():
    with pytest.raises(AnalysisError, match="invalid market report"):
        parse_market_report(MARKET_REPORT.replace('"low"', '"확신함"'))


def test_parse_market_report_rejects_a_horizon_outside_the_range():
    with pytest.raises(AnalysisError, match="invalid market report"):
        parse_market_report(MARKET_REPORT.replace('"horizon_days": 5', '"horizon_days": 900'))


def test_a_report_built_only_from_analyst_numbers_passes():
    report = parse_market_report(MARKET_REPORT)

    assert unsupported_numbers(report, REPORTS) == ()


def test_a_report_that_invents_a_number_is_caught():
    """모델이 데이터를 보지 않고도 그럴듯한 문장을 만드는 것을 막는 자동 검사다."""
    invented = parse_market_report(MARKET_REPORT.replace("0.06으로 낮다", "0.87으로 높다"))

    assert unsupported_numbers(invented, REPORTS) == ("0.87",)


def test_the_claim_horizon_is_not_treated_as_an_invented_number():
    # `horizon_days`는 근거 수치가 아니라 주장의 기간이다. 본문에 나와도 걸리면 안 된다.
    report = parse_market_report(MARKET_REPORT.replace("낮다.", "낮다. 앞으로 5거래일을 본다."))

    assert unsupported_numbers(report, REPORTS) == ()


def test_a_rounded_quote_of_a_supported_number_is_not_a_hallucination():
    """`1.70498658`을 근거로 받아 본문에 `1.70`이라 쓰는 것은 정상이다.

    소수 6자리에서 자르던 포매터가 이 값을 `1.704987`로 만들어 정상 리포트를 반려시켰다.
    """
    fx = AnalystReport.model_validate(
        {
            "observations": [
                {
                    "statement": "USDJPY가 올랐다.",
                    "series": ["yahoo:USDJPY"],
                    "numbers": [{"name": "change", "value": 1.70498658}],
                }
            ],
            "summary": "",
        }
    )

    for written in ("1.70498658", "1.705", "1.70", "1.7"):
        report = parse_market_report(MARKET_REPORT.replace("낮다.", f"낮다. 엔은 {written} 올랐다."))
        assert unsupported_numbers(report, [*REPORTS, ("fx", fx)]) == (), written


def test_a_large_number_is_not_flagged_because_of_scientific_notation():
    """`f"{value:g}"`는 -3049225를 -3.04922e+06으로 바꾼다.

    본문에는 `-3049225`로 적히므로 대조가 어긋나 정상 리포트가 반려된다. 실제로 투자자
    순매수 수량에서 이 오탐이 났다(2026-08-15).
    """
    flow = AnalystReport.model_validate(
        {
            "observations": [
                {
                    "statement": "외국인이 순매수했다.",
                    "series": ["kis:005930"],
                    "numbers": [{"name": "individual_net_buy_qty", "value": -3049225}],
                }
            ],
            "summary": "",
        }
    )
    report = parse_market_report(MARKET_REPORT.replace("낮다.", "낮다. 개인은 -3049225주를 순매도했다."))

    assert unsupported_numbers(report, [*REPORTS, ("flow", flow)]) == ()
