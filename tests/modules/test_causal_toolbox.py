"""인과 그래프 툴박스 — 창 인자, 상한, 대상 검증.

**모델이 고쳐 부를 수 있는 것만 `ToolLimitExceeded`다.** 나머지는 그대로 올라가 태스크를
죽인다 — `handle_tool_errors=True`(기본값)가 DB 연결 끊김을 "결과 없음"으로 위장하는 것을
막는 규칙이다.

계약은 `docs/analysis/market-causal-graph.md` §5.2다.
"""

from datetime import date
from decimal import Decimal

import pytest

from modules.causal import domain, toolbox
from tests.modules.test_causal import FakeConnection

WINDOW = domain.window_for(date(2026, 8, 10))
TARGETS = (
    domain.CausalTarget(kind=domain.CausalTargetKind.INDEX, code="KOSPI"),
    domain.CausalTarget(kind=domain.CausalTargetKind.INSTRUMENT, code="005930"),
    domain.CausalTarget(
        kind=domain.CausalTargetKind.INDICATOR, code="KTB10Y", provider="ecos"
    ),
)


def _box(**results) -> toolbox.CausalToolbox:
    return toolbox.CausalToolbox(
        connection=FakeConnection(results=results),
        window=WINDOW,
        targets=TARGETS,
    )


class TestPriceWindow:
    """세 숫자로 접힌 실현 등락을 편다. **사건 전 구간이 이 툴의 목적이다**(설계 §9)."""

    def test_it_reads_the_table_that_matches_the_target_kind(self) -> None:
        box = _box(**{"FROM index_daily": [(date(2026, 8, 10), Decimal("6299.66"))]})

        result = box.price_window(target_code="KOSPI", days_before=5)

        assert result.code == "KOSPI"
        assert result.rows[0].close == pytest.approx(6299.66)

    def test_a_stock_reads_the_investor_trade_table(self) -> None:
        """국내 종목 일봉은 `stock_daily`가 아니라 수급 테이블이 갖는다."""
        box = _box(
            **{"FROM stock_investor_trade_daily": [(date(2026, 8, 10), Decimal(230000))]}
        )

        box.price_window(target_code="005930", days_before=5)

        assert any("stock_investor_trade_daily" in call[0] for call in box._connection.calls)

    def test_an_indicator_binds_its_provider(self) -> None:
        """`series_id`는 제공처 안에서만 고유하다. 하나로 걸면 제공처가 늘 때 조용히 틀린다."""
        box = _box(**{"FROM indicator_observation": [(date(2026, 8, 10), Decimal("4.239"))]})

        box.price_window(target_code="KTB10Y", days_before=5)

        call = next(c for c in box._connection.calls if "FROM indicator_observation" in c[0])
        assert call[1]["provider"] == "ecos"

    def test_the_window_starts_before_the_event_week(self) -> None:
        """`days_before`가 사건 전 며칠을 볼지 정한다. 그것이 선반영을 보는 유일한 길이다."""
        box = _box(**{"FROM index_daily": []})

        box.price_window(target_code="KOSPI", days_before=10)

        call = next(c for c in box._connection.calls if "FROM index_daily" in c[0])
        assert call[1]["start"] == date(2026, 7, 31)
        assert call[1]["end"] == WINDOW.reaction_end

    def test_a_target_outside_the_list_is_refused(self) -> None:
        """모델이 고쳐 부를 수 있는 실수다. 태스크를 죽이지 않는다."""
        box = _box()

        with pytest.raises(toolbox.ToolLimitExceeded):
            box.price_window(target_code="TSLA", days_before=5)

    def test_too_many_days_is_refused(self) -> None:
        box = _box()

        with pytest.raises(toolbox.ToolLimitExceeded):
            box.price_window(target_code="KOSPI", days_before=toolbox.MAX_DAYS_BEFORE + 1)


class TestInvestorFlow:
    """`수급` 채널을 숫자로 확인한다. 전에는 종가 셋으로 추론했다."""

    def test_it_gives_the_five_investor_columns(self) -> None:
        box = _box(
            **{
                "FROM stock_investor_trade_daily": [
                    (date(2026, 8, 12), Decimal(255500), 5802466, 776871, -6022669, -115933, 12)
                ]
            }
        )

        result = box.investor_flow(stock_code="005930")

        assert result.rows[0].foreign_net_buy == 5802466
        assert result.rows[0].pension_fund_net_buy == -115933

    def test_a_code_that_is_not_a_watched_stock_is_refused(self) -> None:
        """지수·매크로에는 투자자별 수급이 없다. 종목만 받는다."""
        box = _box()

        with pytest.raises(toolbox.ToolLimitExceeded):
            box.investor_flow(stock_code="KOSPI")


class TestPastPaths:
    """어휘 후보가 이름만 주던 것을 실제 쓰임으로 바꾼다."""

    def test_it_only_looks_before_the_target_week(self) -> None:
        """같은 주 자기 경로를 보여 주면 아직 만들지도 않은 것을 참조하게 된다."""
        box = _box(**{"FROM market_causal_path": []})

        box.past_paths(target_code="KOSPI", weeks=4)

        call = next(c for c in box._connection.calls if "FROM market_causal_path" in c[0])
        assert call[1]["week_start"] == WINDOW.week_start
        assert call[1]["since"] == date(2026, 7, 13)

    def test_too_many_weeks_is_refused(self) -> None:
        box = _box()

        with pytest.raises(toolbox.ToolLimitExceeded):
            box.past_paths(target_code="KOSPI", weeks=toolbox.MAX_PAST_WEEKS + 1)


class TestToolDefinitions:
    """`StructuredTool`이 `args_schema`에서 JSON Schema를 뽑는다. 손으로 안 쓴다."""

    def test_three_tools_are_exposed(self) -> None:
        box = _box()

        assert [tool.name for tool in box.tools] == [
            "price_window",
            "investor_flow",
            "past_paths",
        ]

    def test_every_tool_carries_an_args_schema(self) -> None:
        box = _box()

        for tool in box.tools:
            assert tool.args_schema is not None

    def test_the_limits_reach_the_model_through_the_field_description(self) -> None:
        """상한은 코드 상수가 원본이다. 두 곳에 숫자를 적으면 반드시 어긋난다."""
        field = toolbox.PriceWindowArgs.model_fields["days_before"]

        assert str(toolbox.MAX_DAYS_BEFORE) in (field.description or "")


def test_no_tool_schema_puts_description_next_to_a_ref() -> None:
    """중첩 모델에 `Field(description=...)`을 붙이면 스키마가 `$ref` 옆에 `description`을
    두는데 OpenAI가 그것을 거절한다.

        Invalid schema for function: $ref cannot have keywords {'description'}

    응답 스키마에서 실제로 터졌던 사고(2026-08-27)라 툴 스키마에도 같은 가드를 건다.
    **가짜 모델로는 절대 안 잡힌다.**
    """

    def offenders(node, path=""):
        found = []
        if isinstance(node, dict):
            if "$ref" in node and len(node) > 1:
                found.append(f"{path}: {sorted(set(node) - {'$ref'})}")
            for key, value in node.items():
                found += offenders(value, f"{path}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found += offenders(value, f"{path}[{index}]")
        return found

    for tool in _box().tools:
        schema = tool.args_schema.model_json_schema()
        assert not offenders(schema, tool.name), offenders(schema, tool.name)
