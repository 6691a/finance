"""실행 원장 라우트의 라우팅·매핑·직렬화.

`test_routes.py`와 같은 형태다 — 가짜는 리포지토리 자리에 두고 진짜 서비스가 그 위에서
돈다. 없는 것은 세션뿐이다.
"""

from typing import Any

import httpx
import pytest
from dependency_injector import providers

from apps.api.app import create_app
from apps.api.repository import LlmRunDetailRows, LlmRunListRows
from apps.api.repository.llm_run import LlmRunReadRepository
from apps.core.utility import KST
from apps.models.analysis import LlmRunKind, LlmRunStatus
from tests.api.conftest import (
    container,
    llm_run_row,
    outcome_row,
    thesis_row,
    tool_call_row,
)


class FakeRepository:
    """행 묶음만 돌려준다. 그 위의 진짜 서비스가 응답 계약을 만든다."""

    def __init__(self, **rows: Any) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def list_rows(self, **kwargs: Any) -> LlmRunListRows:
        self.calls.append(kwargs)
        return LlmRunListRows(
            runs=tuple(self.rows.get("runs", [])),
            has_more=self.rows.get("has_more", False),
            produced=self.rows.get("produced", {}),
        )

    async def detail_rows(self, llm_run_id: int) -> LlmRunDetailRows | None:
        run = next((row for row in self.rows.get("runs", []) if row.id == llm_run_id), None)
        if run is None:
            return None
        return LlmRunDetailRows(
            run=run,
            tool_calls=tuple(self.rows.get("tool_calls", [])),
            theses=tuple(self.rows.get("theses", [])),
            outcomes=tuple(self.rows.get("outcomes", [])),
            subjects={row.id: row for row in self.rows.get("subjects", [])},
        )

    async def tool_call_row(self, llm_run_id: int, seq: int) -> Any:
        return next(
            (
                row
                for row in self.rows.get("tool_calls", [])
                if row.llm_run_id == llm_run_id and row.seq == seq
            ),
            None,
        )


def app_with(fake: FakeRepository):
    built = container()
    built.llm_run_repository.override(providers.Object(fake))
    return create_app(built)


def client(**rows: Any) -> httpx.AsyncClient:
    app = app_with(FakeRepository(**rows))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_the_service_arrives_by_injection_not_a_lookup():
    """마커가 안 풀리면 주입 자리에 `Provide` 객체가 들어와 조용히 틀린다."""
    async with client(runs=[llm_run_row()]) as http:
        reply = await http.get("/api/llm-runs")

    assert reply.status_code == 200
    assert reply.json()["items"][0]["id"] == 9


@pytest.mark.asyncio
async def test_the_window_filters_the_execution_day_not_the_target_day():
    """T+5 해설의 `run_date`는 과거 원 추론일이다. 그것으로 거르면 오늘 실행한 해설이 빠진다."""
    fake = FakeRepository(runs=[])
    app = app_with(fake)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        await http.get("/api/llm-runs", params={"from": "2026-08-20", "to": "2026-08-21"})

    window = fake.calls[0]
    # KST 날짜 경계를 UTC 시각으로 바꿔서 넘긴다 — 조회문에는 날짜 함수가 없다.
    assert window["started_from"].astimezone(KST).isoformat() == "2026-08-20T00:00:00+09:00"
    assert window["started_to"].astimezone(KST).isoformat() == "2026-08-22T00:00:00+09:00"


@pytest.mark.asyncio
async def test_the_default_window_is_two_weeks_of_kst_days():
    fake = FakeRepository(runs=[])
    app = app_with(fake)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        await http.get("/api/llm-runs")

    window = fake.calls[0]
    assert (window["started_to"] - window["started_from"]).days == 14


@pytest.mark.asyncio
async def test_there_is_no_subject_filter_on_runs():
    """대화 하나가 여러 대상을 다루고 실패 대화에는 산출물이 아예 없다."""
    async with client(runs=[]) as http:
        reply = await http.get("/api/llm-runs", params={"subject_code": "KOSPI"})

    # 모르는 파라미터는 무시된다. 필터로 먹히지 않는다는 것이 요점이다.
    assert reply.status_code == 200


@pytest.mark.asyncio
async def test_a_run_that_never_recorded_its_end_has_no_duration():
    """`running`은 "시작했지만 종료를 기록하지 못했다"이기도 하다. 지금 시각으로 채우면
    조회할 때마다 값이 변한다."""
    async with client(runs=[llm_run_row(status=LlmRunStatus.RUNNING)]) as http:
        item = (await http.get("/api/llm-runs")).json()["items"][0]

    assert item["status"] == "running"
    assert item["finished_at"] is None
    assert item["duration_ms"] is None


@pytest.mark.asyncio
async def test_a_finished_run_reports_its_duration_in_milliseconds():
    async with client(runs=[llm_run_row()]) as http:
        item = (await http.get("/api/llm-runs")).json()["items"][0]

    assert item["duration_ms"] == 90_000


@pytest.mark.asyncio
async def test_the_list_never_carries_tool_arguments():
    """목록은 건수만 낸다. 인자·결과는 상세와 단건이 준다."""
    async with client(runs=[llm_run_row()]) as http:
        item = (await http.get("/api/llm-runs")).json()["items"][0]

    assert item["tool_call_count"] == 11
    assert "tool_calls" not in item
    assert "arguments" not in str(item)


@pytest.mark.asyncio
async def test_a_missing_run_is_a_404_not_an_empty_body():
    async with client(runs=[]) as http:
        assert (await http.get("/api/llm-runs/999")).status_code == 404
        assert (await http.get("/api/llm-runs/999/tool-calls/1")).status_code == 404


@pytest.mark.asyncio
async def test_the_detail_lists_the_calls_without_their_results():
    """결과 전문을 목록에 실으면 왕복 하나가 수 MB가 된다."""
    rows = {"runs": [llm_run_row()], "tool_calls": [tool_call_row(1), tool_call_row(2, round_no=2)]}
    async with client(**rows) as http:
        payload = (await http.get("/api/llm-runs/9")).json()

    assert [call["seq"] for call in payload["tool_calls"]] == [1, 2]
    assert [call["round_no"] for call in payload["tool_calls"]] == [1, 2]
    assert "result" not in payload["tool_calls"][0]
    # 상세로 가는 링크는 응답이 만든다. 프런트가 경로를 조립하지 않는다.
    assert payload["tool_calls"][0]["url"] == "/api/llm-runs/9/tool-calls/1"


@pytest.mark.asyncio
async def test_the_raw_and_validated_arguments_stay_side_by_side():
    """unknown tool·인자 검증 실패는 함수에 진입하지 않아 후자가 null이다."""
    rows = {"runs": [llm_run_row()], "tool_calls": [tool_call_row(1), tool_call_row(2, failed=True)]}
    async with client(**rows) as http:
        calls = (await http.get("/api/llm-runs/9")).json()["tool_calls"]

    assert calls[0]["arguments"] == {"limit": 5}
    assert calls[0]["validated_arguments"] == {"limit": 5, "kind": "document"}
    assert calls[1]["validated_arguments"] is None
    assert calls[1]["error_kind"] == "validation"
    assert calls[1]["duration_ms"] is None


@pytest.mark.asyncio
async def test_a_result_the_model_never_saw_is_marked_as_such():
    """`delivered = false`는 실행됐지만 모델이 못 본 것이다. 모델의 입력으로 읽으면 안 된다."""
    rows = {"runs": [llm_run_row()], "tool_calls": [tool_call_row(1, delivered=False)]}
    async with client(**rows) as http:
        call = (await http.get("/api/llm-runs/9")).json()["tool_calls"][0]

    assert call["delivered"] is False
    assert call["error"] is None


@pytest.mark.asyncio
async def test_the_single_tool_call_is_the_only_place_the_result_lives():
    async with client(runs=[llm_run_row()], tool_calls=[tool_call_row(1)]) as http:
        payload = (await http.get("/api/llm-runs/9/tool-calls/1")).json()

    assert payload["result"] == '{"rows": []}'
    assert payload["seq"] == 1


@pytest.mark.asyncio
async def test_a_failed_call_carries_the_error_instead_of_a_result():
    """`result`와 `error`는 배타다(DB CHECK). 화면이 둘을 동시에 보이지 않게 한다."""
    async with client(runs=[llm_run_row()], tool_calls=[tool_call_row(1, failed=True)]) as http:
        payload = (await http.get("/api/llm-runs/9/tool-calls/1")).json()

    assert payload["result"] is None
    assert payload["error"] == "limit must be <= 20"


@pytest.mark.asyncio
async def test_a_generation_run_links_its_theses_and_a_narration_run_its_outcomes():
    """생성과 해설은 산출물의 종류가 다르다. 둘 중 하나는 언제나 빈 배열이다."""
    async with client(runs=[llm_run_row()], theses=[thesis_row()]) as http:
        generation = (await http.get("/api/llm-runs/9")).json()

    assert [row["id"] for row in generation["produced_theses"]] == [1]
    assert generation["narrated_outcomes"] == []
    assert generation["produced_count"] == 1

    rows = {
        "runs": [llm_run_row(10, LlmRunKind.NARRATION)],
        "outcomes": [outcome_row(1, narration_run_id=10)],
        "subjects": [thesis_row()],
    }
    async with client(**rows) as http:
        narration = (await http.get("/api/llm-runs/10")).json()

    assert narration["produced_theses"] == []
    assert narration["narrated_outcomes"][0]["horizon_days"] == 1
    assert narration["narrated_outcomes"][0]["label"] == "코스피"
    assert narration["narrated_outcomes"][0]["verdict"] == "supported"


@pytest.mark.asyncio
async def test_a_failed_run_still_shows_what_it_managed_to_record():
    """실패 전 툴 기록과 마지막 오류가 남는다. 산출물만 비어 있다."""
    rows = {
        "runs": [llm_run_row(status=LlmRunStatus.FAILED)],
        "tool_calls": [tool_call_row(1)],
    }
    async with client(**rows) as http:
        payload = (await http.get("/api/llm-runs/9")).json()

    assert payload["status"] == "failed"
    assert payload["error"] == "모델이 붙지 않았다"
    assert len(payload["tool_calls"]) == 1
    assert payload["produced_theses"] == []


@pytest.mark.asyncio
async def test_times_end_with_z_not_an_offset():
    async with client(runs=[llm_run_row()]) as http:
        item = (await http.get("/api/llm-runs")).json()["items"][0]

    assert item["started_at"].endswith("Z")
    assert "+00:00" not in item["started_at"]


def test_the_list_statement_reads_one_more_row_than_the_page():
    """총 건수를 세지 않고 다음 쪽이 있는지만 본다."""
    from datetime import UTC, datetime

    statement = LlmRunReadRepository.list_statement(
        started_from=datetime(2026, 8, 20, tzinfo=UTC),
        started_to=datetime(2026, 8, 22, tzinfo=UTC),
        limit=50,
    )
    compiled = str(statement.compile(compile_kwargs={"literal_binds": True}))

    assert "LIMIT 51" in compiled
    assert "ORDER BY thesis_llm_run.started_at DESC" in compiled
