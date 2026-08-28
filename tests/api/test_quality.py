"""품질 집계의 계약. **표 둘이 섞이지 않는 것이 이 파일의 주제다.**

예측 품질의 키에는 `thesis`의 모델·판만, 해설 품질의 키에는 `thesis_outcome`의 모델·판만
들어간다. 합치면 "판 7이 이유 지지율도 올렸다"로 읽히는데 그 손잡이는 움직인 적이 없다.
"""

from datetime import date
from typing import Any

import httpx
import pytest
from dependency_injector import providers

from apps.api.app import create_app
from apps.api.repository import ForecastGrade, ForecastRunStat, NarrativeGrade, QualityRows
from apps.api.repository.quality import QualityReadRepository
from apps.api.schemas import UNIFORM_BRIER
from tests.api.conftest import container

WEEK = date(2026, 8, 24)


class FakeRepository:
    def __init__(self, **rows: Any) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def quality_rows(self, **kwargs: Any) -> QualityRows:
        self.calls.append(kwargs)
        return QualityRows(
            forecast_grades=tuple(self.rows.get("grades", [])),
            forecast_runs=tuple(self.rows.get("runs", [])),
            narrative=tuple(self.rows.get("narrative", [])),
        )


def app_with(fake: FakeRepository):
    built = container()
    built.quality_repository.override(providers.Object(fake))
    return create_app(built)


def client(**rows: Any) -> httpx.AsyncClient:
    app = app_with(FakeRepository(**rows))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def grade(prompt_version: str = "7", horizon: int = 0, **overrides: Any) -> ForecastGrade:
    values: dict[str, Any] = {
        "week_start": WEEK,
        "horizon_days": horizon,
        "run_slot": "pre_open",
        "llm_model": "grok-4.6",
        "prompt_version": prompt_version,
        "mean_brier": 0.51,
        "brier_samples": 6,
        "mean_return_error_pct": 0.3,
        "mae_return_pct": 0.4,
        "return_samples": 4,
    }
    return ForecastGrade(**(values | overrides))


def run_stat(prompt_version: str = "7", horizon: int = 0, **overrides: Any) -> ForecastRunStat:
    values: dict[str, Any] = {
        "week_start": WEEK,
        "horizon_days": horizon,
        "run_slot": "pre_open",
        "llm_model": "grok-4.6",
        "prompt_version": prompt_version,
        "mean_tool_calls": 11.0,
        "mean_tool_result_chars": 54555.0,
        "run_samples": 3,
    }
    return ForecastRunStat(**(values | overrides))


def narrative(prompt_version: str = "2/informed", horizon: int = 1, **overrides: Any) -> NarrativeGrade:
    values: dict[str, Any] = {
        "week_start": WEEK,
        "horizon_days": horizon,
        "llm_model": "grok-4.6",
        "prompt_version": prompt_version,
        "supported": 4,
        "contradicted": 1,
        "unresolved": 2,
        "verdict_samples": 7,
    }
    return NarrativeGrade(**(values | overrides))


@pytest.mark.asyncio
async def test_the_static_route_wins_over_the_dynamic_thesis_id():
    """`/api/theses/quality`가 `/api/theses/{thesis_id}`에 먹히면 422가 된다.

    라우트 집합만 보면 이 사고를 못 잡는다 — 실제 요청을 보내야 순서가 확인된다.
    """
    async with client() as http:
        reply = await http.get("/api/theses/quality")

    assert reply.status_code == 200
    assert set(reply.json()) == {"forecast", "narrative", "uniform_brier"}


@pytest.mark.asyncio
async def test_the_two_tables_key_on_different_prompt_versions():
    """원 추론 판을 바꿔도 해설 표의 행이 갈라지지 않고, 반대도 같다."""
    rows = {
        "grades": [grade("7"), grade("8")],
        "narrative": [narrative("2/informed")],
    }
    async with client(**rows) as http:
        payload = (await http.get("/api/theses/quality")).json()

    assert [row["prompt_version"] for row in payload["forecast"]] == ["7", "8"]
    # 원 추론 판이 둘이어도 해설 표는 한 행이다.
    assert [row["prompt_version"] for row in payload["narrative"]] == ["2/informed"]
    # 해설 표에는 슬롯이 없다 — 해설은 슬롯이 아니라 지평으로 갈린다.
    assert "run_slot" not in payload["narrative"][0]


@pytest.mark.asyncio
async def test_a_narrative_version_change_never_splits_the_forecast_table():
    rows = {"grades": [grade("7")], "narrative": [narrative("2/informed"), narrative("3/plain")]}
    async with client(**rows) as http:
        payload = (await http.get("/api/theses/quality")).json()

    assert len(payload["forecast"]) == 1
    assert len(payload["narrative"]) == 2


@pytest.mark.asyncio
async def test_each_metric_carries_its_own_sample_count():
    """결측 조건이 달라 표본 수가 다르다. 하나로 합치면 평균 옆의 n이 거짓이 된다."""
    async with client(grades=[grade()], runs=[run_stat()]) as http:
        row = (await http.get("/api/theses/quality")).json()["forecast"][0]

    assert (row["brier_samples"], row["return_samples"], row["run_samples"]) == (6, 4, 3)


@pytest.mark.asyncio
async def test_a_missing_metric_stays_null_and_never_becomes_zero():
    """`null`을 0으로 바꾸면 "안 재 봤다"가 "완벽했다"로 읽힌다."""
    empty = grade(mean_brier=None, brier_samples=0, mean_return_error_pct=None, mae_return_pct=None, return_samples=0)
    async with client(grades=[empty]) as http:
        row = (await http.get("/api/theses/quality")).json()["forecast"][0]

    assert row["mean_brier"] is None
    assert row["mae_return_pct"] is None
    assert row["beats_uniform"] is None
    # 툴 집계가 없는 키는 평균이 null이고 표본이 0이다.
    assert row["mean_tool_calls"] is None
    assert row["run_samples"] == 0


@pytest.mark.asyncio
async def test_the_uniform_baseline_is_a_comparison_not_a_grade():
    """균등 확률의 3-class Brier가 약 0.667이다. 그 한 줄 비교만 낸다."""
    rows = {"grades": [grade(mean_brier=0.4), grade(horizon=1, mean_brier=0.9)]}
    async with client(**rows) as http:
        payload = (await http.get("/api/theses/quality")).json()

    assert payload["uniform_brier"] == pytest.approx(UNIFORM_BRIER)
    assert [row["beats_uniform"] for row in payload["forecast"]] == [True, False]


@pytest.mark.asyncio
async def test_there_is_no_combined_score():
    """Brier(방향)·크기 오차(폭)·verdict(이유)는 서로 다른 것을 재고 단위도 다르다."""
    async with client(grades=[grade()], narrative=[narrative()]) as http:
        payload = (await http.get("/api/theses/quality")).json()

    keys = set(payload["forecast"][0]) | set(payload["narrative"][0])
    assert not [key for key in keys if "score" in key and key != "mean_brier"]
    assert "overall" not in str(keys)


@pytest.mark.asyncio
async def test_the_default_window_is_four_weeks():
    """주 단위 집계라 목록의 14일보다 길다. 네 주가 최소 비교 단위다."""
    fake = FakeRepository()
    app = app_with(fake)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        await http.get("/api/theses/quality")

    window = fake.calls[0]
    assert (window["run_date_to"] - window["run_date_from"]).days == 27


@pytest.mark.asyncio
async def test_the_filters_reach_the_repository():
    fake = FakeRepository()
    app = app_with(fake)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        await http.get(
            "/api/theses/quality",
            params={"slot": "pre_open", "subject_code": "KOSPI", "horizon_days": [0, 5]},
        )

    call = fake.calls[0]
    assert call["run_slots"] == ["pre_open"]
    assert call["subject_codes"] == ["KOSPI"]
    assert call["horizon_days"] == [0, 5]


def test_the_tool_average_dedupes_the_run_before_averaging():
    """실행 하나가 여러 추론을 만든다. 조인 결과에서 그대로 평균을 내면 추론이 많은
    실행이 그 수만큼 가중된다."""
    compiled = str(
        QualityReadRepository.forecast_run_statement(
            run_date_from=date(2026, 8, 1),
            run_date_to=date(2026, 8, 27),
            run_slots=(),
            subject_codes=(),
            horizon_days=(),
        ).compile(compile_kwargs={"literal_binds": True})
    )

    assert "DISTINCT" in compiled
    # 실패·중단 실행의 툴 수를 평균에 넣지 않는다.
    assert "succeeded" in compiled


def test_the_forecast_key_uses_the_thesis_model_and_the_narrative_key_the_outcome_model():
    """두 조회가 같은 테이블의 같은 칸을 보면 표 둘을 나눈 뜻이 사라진다."""
    scope = {
        "run_date_from": date(2026, 8, 1),
        "run_date_to": date(2026, 8, 27),
        "run_slots": (),
        "subject_codes": (),
        "horizon_days": (),
    }
    forecast = str(QualityReadRepository.forecast_grade_statement(**scope).compile())
    narrative_sql = str(QualityReadRepository.narrative_statement(**scope).compile())

    assert "thesis.llm_model" in forecast
    assert "thesis_outcome.llm_model" not in forecast
    assert "thesis_outcome.llm_model" in narrative_sql
    assert "thesis.llm_model" not in narrative_sql
    # 해설은 슬롯으로 갈리지 않는다.
    assert "GROUP BY" in narrative_sql
    assert "run_slot" not in narrative_sql.split("GROUP BY")[1]
