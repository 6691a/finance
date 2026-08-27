"""주간 인과 그래프 DAG.

흐름의 알맹이는 `modules/causal/`에 있고 `tests/modules/test_causal*.py`가 덮는다.
여기 남은 것은 스케줄, Param 계약, 그리고 실패 분류다.
"""

from datetime import UTC, datetime

import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import market_causal_weekly as dag_module
from modules.causal import domain

DAG = dag_module.market_causal_weekly


def test_it_runs_weekly_on_monday_morning_kst():
    """`W+2` 월요일에 돈다. 장과 무관하므로 시각 자체에 뜻은 없고 주말 뒤 첫 실행이 조건이다."""
    assert DAG.schedule == "0 7 * * 1"
    assert str(DAG.timetable.timezone) == "Asia/Seoul"
    assert DAG.max_active_runs == 1


def test_the_start_date_is_timezone_aware_kst():
    """naive datetime을 쓰지 않는다(저장소 규칙)."""
    assert str(DAG.start_date.tzinfo) in ("Asia/Seoul", "UTC")
    assert DAG.start_date.tzinfo is not None


def test_the_display_metadata_is_filled_in():
    """화면용 메타데이터는 빈 문자열로 두지 않는다(저장소 규칙)."""
    assert DAG.dag_display_name.strip()
    assert DAG.description.strip()
    assert DAG.doc_md and "W+2" in DAG.doc_md


def test_the_only_param_is_the_target_week():
    """슬롯이 없으므로 `resolve_slot` 같은 장치가 필요 없다. 주 하나만 받는다."""
    params = DAG.params

    assert set(params) == {"week_start"}
    param = params.get_param("week_start")
    assert param.schema["title"].strip()
    assert param.description.strip()
    assert dict(params)["week_start"] is None  # 비우면 실행 주에서 계산한다


def test_there_is_exactly_one_task():
    """후보 조립부터 저장까지 한 흐름이다. 나누면 XCom으로 후보 수십 건을 넘기게 된다."""
    assert [task.task_id for task in DAG.tasks] == ["build_causal_graph"]


class TestFailureClassification:
    """되돌릴 수 없는 오류는 즉시 죽이고, 재시도할 값어치가 있는 것은 그대로 올린다."""

    @staticmethod
    def _run(monkeypatch: pytest.MonkeyPatch, error: Exception):
        from modules.causal import run

        def explode(**kwargs):
            raise error

        monkeypatch.setattr(run, "build_weekly_graph", explode)
        task = DAG.get_task("build_causal_graph")
        return task.python_callable(
            logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC),
            params={"week_start": None},
            dag_run=type("Run", (), {"run_id": "manual__test"})(),
        )

    def test_a_bad_param_fails_the_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """화요일을 주면 자연키가 어긋난다. 재시도해도 같은 답이다."""
        with pytest.raises(AirflowFailException):
            self._run(monkeypatch, ValueError("week_start must be a Monday"))

    def test_vocabulary_drift_fails_the_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """정규화가 깨졌다는 신호다. 조용히 넘어가면 다음 주에 어휘가 두 배가 된다."""
        from modules.causal.store import VocabularyDriftError

        with pytest.raises(AirflowFailException):
            self._run(monkeypatch, VocabularyDriftError("no reuse"))

    def test_a_connection_error_is_left_for_airflow_to_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """재시도할 값어치가 있다. 삼켜서 성공으로 만들지 않는다."""
        with pytest.raises(ConnectionError):
            self._run(monkeypatch, ConnectionError("provider down"))


def test_the_run_lag_matches_the_schedule():
    """스케줄이 월요일이고 `RUN_LAG_WEEKS`가 2라야 `W+2` 규칙이 성립한다."""
    assert domain.RUN_LAG_WEEKS == 2
