"""품질 집계의 매핑.

**나눗셈이 서비스에 있다.** 리포지토리가 합과 건수를 주고 여기서 한 번만 나눈다 —
그래야 주를 합칠 때 평균의 평균이 되지 않는다.

**표본이 0이면 비율은 `None`이다.** 0.0으로 두면 "다 틀렸다"로 읽힌다.
"""

from datetime import date
from typing import Any

import httpx
import pytest
from dependency_injector import providers

from apps.api.app import create_app
from apps.api.repository import ForecastGrade, QualityRows, ReviewStat
from apps.api.service.quality import build_summary
from tests.api.conftest import container

WEEK = date(2026, 8, 31)


def grade(**overrides: Any) -> ForecastGrade:
    values: dict[str, Any] = {
        "week_start": WEEK,
        "slot": "midday",
        "llm_model": "grok-4.6",
        "prompt_version": "3",
        "graded": 4,
        "pending": 1,
        "hits": 3,
        "within_band": 2,
        "abs_error_sum": 6.0,
        "band_sum": 5.6,
        "expected_sum": 2.4,
        "weak": 0,
        "rejected_reasons": 1,
    }
    values.update(overrides)
    return ForecastGrade(**values)


def review(**overrides: Any) -> ReviewStat:
    values: dict[str, Any] = {
        "week_start": WEEK,
        "llm_model": "grok-4.6",
        "prompt_version": "1",
        "runs": 4,
        "observations_written": 14,
        "memories_written": 5,
        "memories_rejected": 0,
        "memories_dropped": 2,
        "memories_expired": 1,
        "rejected": 0,
        "tool_calls_sum": 60,
        "truncated": 0,
    }
    values.update(overrides)
    return ReviewStat(**values)


class FakeQuality:
    def __init__(self, rows: QualityRows) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def summary_rows(self, **kwargs: Any) -> QualityRows:
        self.calls.append(kwargs)
        return self.rows


def client(rows: QualityRows) -> httpx.AsyncClient:
    built = container()
    built.quality_repository.override(providers.Object(FakeQuality(rows)))
    app = create_app(built)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def test_the_rates_are_computed_once_at_the_end():
    row = build_summary(QualityRows(forecast=(grade(),))).forecast[0]

    assert row.hit_rate == 0.75
    assert row.band_rate == 0.5
    assert row.mean_abs_error == 1.5
    assert row.mean_band_pct == 1.4


def test_a_row_with_nothing_graded_says_null_not_zero():
    """0.0으로 두면 "다 틀렸다"로 읽힌다. 아직 안 쟀다는 것과 다르다."""
    row = build_summary(
        QualityRows(forecast=(grade(graded=0, hits=0, within_band=0, abs_error_sum=None),))
    ).forecast[0]

    assert row.hit_rate is None
    assert row.band_rate is None
    assert row.mean_abs_error is None
    assert row.beats_coin_flip is None
    assert row.pending == 1


def test_the_coin_flip_comparison_is_one_line_not_a_grade():
    """방향이 둘뿐이라 기준이 0.5다. 등급을 매기지 않는다."""
    good = build_summary(QualityRows(forecast=(grade(graded=4, hits=3),))).forecast[0]
    bad = build_summary(QualityRows(forecast=(grade(graded=4, hits=2),))).forecast[0]

    assert good.beats_coin_flip is True
    # 정확히 0.5는 "넘지 못했다"다 — 찍기와 같은 성적이다.
    assert bad.beats_coin_flip is False


def test_the_band_is_compared_against_the_error_it_has_to_cover():
    """폭이 오차보다 작으면 구조적으로 못 맞힌다. 판 3이 그 자리를 고쳤다."""
    row = build_summary(
        QualityRows(forecast=(grade(graded=4, abs_error_sum=10.0, band_sum=4.0),))
    ).forecast[0]

    assert row.mean_abs_error == 2.5
    assert row.mean_band_pct == 1.0
    assert row.mean_band_pct < row.mean_abs_error


def test_the_review_row_divides_by_runs_not_by_observations():
    row = build_summary(QualityRows(review=(review(),))).review[0]

    assert row.mean_observations == 3.5
    assert row.mean_tool_calls == 15.0


def test_a_review_week_with_no_runs_says_null():
    row = build_summary(
        QualityRows(review=(review(runs=0, observations_written=0, tool_calls_sum=0),))
    ).review[0]

    assert row.mean_observations is None
    assert row.mean_tool_calls is None


@pytest.mark.asyncio
async def test_the_two_tables_stay_two_tables_in_one_response():
    """키가 서로 다른 판이다. 한 행에 놓으면 전망 판을 올린 효과가 관찰 쪽으로 읽힌다."""
    async with client(QualityRows(forecast=(grade(),), review=(review(),))) as http:
        payload = (await http.get("/api/forecasts/quality")).json()

    assert payload["forecast"][0]["slot"] == "midday"
    assert payload["review"][0]["prompt_version"] == "1"
    assert "slot" not in payload["review"][0]
    assert payload["coin_flip_hit_rate"] == 0.5


@pytest.mark.asyncio
async def test_the_window_defaults_to_four_weeks():
    """주 단위 집계라 목록의 14일보다 길다 — 네 주가 최소 비교 단위다."""
    fake = FakeQuality(QualityRows())
    built = container()
    built.quality_repository.override(providers.Object(fake))
    app = create_app(built)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        await http.get("/api/forecasts/quality")

    window = fake.calls[0]["run_date_to"] - fake.calls[0]["run_date_from"]
    assert window.days == 27
