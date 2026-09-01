"""주간 인과 그래프 DAG.

흐름의 알맹이는 `modules/causal/`에 있고 `tests/modules/test_causal*.py`가 덮는다.
여기 남은 것은 스케줄, Param 계약, 그리고 실패 분류다.
"""

from datetime import UTC, datetime

import pytest
from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException

from dags import market_causal_weekly as dag_module
from modules.causal import domain

DAG = dag_module.market_causal_weekly

_ENV = {
    "NEO4J_URI": "bolt://neo4j:7687",
    "NEO4J_USER": "neo4j",
    "NEO4J_PASSWORD": "pw",
}


def _payload():
    """엣지 수만 세는 자리라 빈 투영이면 충분하다."""
    from modules.graph import rows

    return rows.GraphPayload(events=(), channels=(), targets=(), from_event=(), from_target=(), chain=(), hits=())


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


def test_the_params_are_the_target_week_and_the_resync_switch():
    """슬롯이 없으므로 `resolve_slot` 같은 장치가 필요 없다. 주 하나와 재동기화 스위치다."""
    params = DAG.params

    assert set(params) == {"week_start", "sync_only"}
    for name in ("week_start", "sync_only"):
        param = params.get_param(name)
        assert param.schema["title"].strip()
        assert param.description.strip()
    assert dict(params)["week_start"] is None  # 비우면 실행 주에서 계산한다
    assert dict(params)["sync_only"] is False  # 보통 실행은 생성 + 투영이다


def test_the_projection_follows_the_build():
    """후보 조립부터 저장까지가 한 태스크고, Neo4j 투영과 방향성 요약이 뒤에 붙는다.

    **두 스토어를 같은 태스크에 넣지 않는다** — Neo4j 쓰기가 실패해도 Postgres 쓰기는 이미
    커밋된 채로 남아야 한다. 분산 트랜잭션을 만들지 않는다.
    """
    assert sorted(task.task_id for task in DAG.tasks) == [
        "build_causal_graph",
        "summarize_direction",
        "sync_graph",
    ]
    assert DAG.get_task("build_causal_graph").downstream_task_ids == {"sync_graph"}
    assert DAG.get_task("sync_graph").downstream_task_ids == {"summarize_direction"}


def test_the_direction_stops_when_the_projection_is_skipped():
    """**`sync_graph`와 반대 판단이다**(설계 §3).

    투영이 skip이면(NEO4J_URI 없음) 읽을 그래프가 없으므로 방향성도 서면 안 된다. 그 skip이
    추론까지 전파되는 것이 §4.2.1이고, 관측 상태의 나이 상한이 그 마지막 자리다.
    """
    assert DAG.get_task("summarize_direction").trigger_rule == "all_success"


def test_the_projection_runs_even_when_the_build_is_skipped():
    """기본 `all_success`는 upstream이 skip이면 downstream도 skip이다.

    `sync_only` 실행은 `build_causal_graph`를 건너뛰므로 `none_failed`라야 투영이 돈다.
    """
    assert DAG.get_task("sync_graph").trigger_rule == "none_failed"


def test_a_manual_trigger_without_logical_date_still_runs(monkeypatch: pytest.MonkeyPatch):
    """`airflow dags trigger`로 부른 실행은 **context에 `logical_date`가 없다**.

    Airflow 3에서 그 값은 스케줄된 실행에만 붙는다. 직접 인덱싱하면 태스크가 시작하자마자
    KeyError로 죽는다 — 2026-08-28 운영 첫 트리거에서 실제로 그랬다. 기존 DAG들은
    `context.get(...) or 벽시계` 형태를 쓴다.
    """
    from modules.causal import run

    seen = {}

    def record(**kwargs):
        seen.update(kwargs)
        return {"week_start": "2026-08-10", "stored": 0, "skipped": False}

    monkeypatch.setattr(run, "build_weekly_graph", record)
    task = DAG.get_task("build_causal_graph")

    task.python_callable(
        params={"week_start": "2026-08-10"},
        dag_run=type("Run", (), {"run_id": "manual__2026-08-28"})(),
    )

    assert seen["week_start_param"] == "2026-08-10"
    assert seen["logical_date"] is not None


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

    def test_incomplete_returns_fail_the_task(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """**반쪽짜리 주는 재시도해도 같다.** T+5 일봉이 들어오기를 기다려 다시 돌린다."""
        from modules.causal.run import IncompleteReturnsError

        with pytest.raises(AirflowFailException) as error:
            self._run(monkeypatch, IncompleteReturnsError("8/10 targets have no returns"))

        assert "no returns" in str(error.value)

    def test_a_connection_error_is_left_for_airflow_to_retry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """재시도할 값어치가 있다. 삼켜서 성공으로 만들지 않는다."""
        with pytest.raises(ConnectionError):
            self._run(monkeypatch, ConnectionError("provider down"))


def test_the_run_lag_matches_the_schedule():
    """스케줄이 월요일이고 `RUN_LAG_WEEKS`가 2라야 `W+2` 규칙이 성립한다."""
    assert domain.RUN_LAG_WEEKS == 2


class TestProjection:
    """`sync_graph`. 무엇을 밀지 정하는 규칙과, 설정이 없을 때의 처신이다."""

    @staticmethod
    def _run(monkeypatch: pytest.MonkeyPatch, summary, *, params=None, env=None):
        from modules.causal import run

        for name in ("NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD"):
            monkeypatch.delenv(name, raising=False)
        for name, value in (env or {}).items():
            monkeypatch.setenv(name, value)

        monkeypatch.setattr(run, "connection", lambda: type("C", (), {"close": lambda self: None})())
        return DAG.get_task("sync_graph").python_callable(summary, params=params or {"sync_only": False})

    def test_a_missing_uri_skips_instead_of_failing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """인스턴스가 서기 전에도 앞 태스크는 정상이어야 한다.

        설정 누락으로 매주 빨간 태스크를 만들면 진짜 실패가 묻힌다.
        """
        with pytest.raises(AirflowSkipException):
            self._run(monkeypatch, {"week_start": "2026-08-10"})

    def test_a_uri_without_credentials_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """URI만 있고 계정이 없는 것은 설정 실수다. 재시도해도 같다."""
        with pytest.raises(AirflowFailException):
            self._run(
                monkeypatch,
                {"week_start": "2026-08-10"},
                env={"NEO4J_URI": "bolt://neo4j:7687"},
            )

    def test_it_projects_the_week_from_the_upstream_summary(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from datetime import date

        from modules.graph import projection as graph

        seen: list[date] = []

        def read(conn, week):
            seen.append(week)
            return ([1], [2])

        monkeypatch.setattr(graph, "read_week", read)
        monkeypatch.setattr(graph, "project", lambda paths, steps: _payload())
        monkeypatch.setattr(graph, "write_graph", lambda uri, auth, payload: None)

        result = self._run(monkeypatch, {"week_start": "2026-08-10"}, env=_ENV)

        assert seen == [date(2026, 8, 10)]
        assert result["weeks"] == ["2026-08-10"]

    def test_sync_only_projects_every_stored_week(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """초기 적재와 밀린 주 복구가 이것 하나다. MERGE라 몇 번을 돌려도 같은 그래프다."""
        from datetime import date

        from modules.graph import projection as graph

        weeks = [date(2026, 8, 10), date(2026, 8, 17)]
        seen: list[date] = []

        def read(conn, week):
            seen.append(week)
            return ([1], [2])

        monkeypatch.setattr(graph, "stored_weeks", lambda conn: weeks)
        monkeypatch.setattr(graph, "read_week", read)
        monkeypatch.setattr(graph, "project", lambda paths, steps: _payload())
        monkeypatch.setattr(graph, "write_graph", lambda uri, auth, payload: None)

        result = self._run(monkeypatch, None, params={"sync_only": True}, env=_ENV)

        assert seen == weeks
        assert result["weeks"] == ["2026-08-10", "2026-08-17"]

    def test_no_week_and_no_sync_only_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """어느 주를 밀지 모르는 채로 도는 것보다 죽는 편이 낫다."""
        with pytest.raises(AirflowFailException):
            self._run(monkeypatch, None, env=_ENV)

    def test_the_build_is_skipped_on_a_sync_only_run(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """재동기화만 하는 실행은 모델을 부르지 않는다. 비용이 있는 쪽이다."""
        with pytest.raises(AirflowSkipException):
            DAG.get_task("build_causal_graph").python_callable(
                logical_date=datetime(2026, 8, 23, 22, 0, tzinfo=UTC),
                params={"week_start": None, "sync_only": True},
                dag_run=type("Run", (), {"run_id": "manual__test"})(),
            )
