"""코스피 전망 모델 — 등록, 어휘 대조, SQL 컬럼 대조.

**Airflow 트리와 백엔드 트리가 같은 어휘를 두 벌로 갖는다.** Airflow는 `apps/`를 import하지
못해 중복이 불가피하고, 그 둘이 어긋나면 저장이 CHECK에서 죽는다. 여기가 그 대조 자리다.

수집기 테스트와 같은 이유로 **INSERT 컬럼도 모델 metadata와 대조한다** — 흐름이 문자열 SQL을
쓰므로 컬럼 이름이 틀려도 가짜 연결 테스트는 통과한다.
"""

import re

from apps.core.database import Base
from apps.models import analysis
from apps.models.analysis.kospi import (
    KospiDirection,
    KospiForecast,
    KospiLlmRun,
    KospiLlmRunKind,
    KospiLlmRunStatus,
    KospiSlot,
    KospiToolCall,
    KospiToolCallErrorKind,
)

MODELS = (KospiForecast, KospiLlmRun, KospiToolCall)


def _without_comments(statement: str) -> str:
    """`--` 주석 줄을 뺀 SQL. 주석에 괄호와 컬럼 이름이 들어 있어 그냥 파싱하면 섞인다."""
    return "\n".join(line for line in statement.splitlines() if not line.lstrip().startswith("--"))


def _insert_columns(statement: str) -> set[str]:
    body = _without_comments(statement)
    inner = body[body.index("(") + 1 : body.index(") VALUES")]
    return {name.strip().rstrip(",") for name in inner.splitlines() if name.strip()}


def _assigned_columns(statement: str) -> set[str]:
    """`SET`과 `WHERE` 사이만 본다. WHERE의 술어도 `컬럼 = %(값)s` 모양이라 함께 잡힌다."""
    body = _without_comments(statement)
    assignments = body[body.index("SET ") : body.index("WHERE ")]
    return set(re.findall(r"(\w+) = %\(", assignments))


def test_the_kospi_models_are_exported_for_autogenerate():
    # __all__에 없으면 Alembic autogenerate가 모델을 보지 못한다(프로젝트 규칙).
    for name in (
        "KospiForecast",
        "KospiLlmRun",
        "KospiToolCall",
        "KospiSlot",
        "KospiDirection",
        "KospiLlmRunKind",
        "KospiLlmRunStatus",
        "KospiToolCallErrorKind",
    ):
        assert name in analysis.__all__


def test_the_kospi_tables_reach_the_metadata():
    for model in MODELS:
        assert model.__tablename__ in Base.metadata.tables


def test_the_kospi_tables_follow_the_search_path():
    # 파일 이름 kospi.py는 도메인 구분일 뿐이라 PostgreSQL 스키마가 되지 않는다.
    for model in MODELS:
        assert model.__table__.schema is None


def test_the_kospi_enums_avoid_postgresql_native_types():
    """native enum은 값 추가·삭제 마이그레이션 비용이 커서 쓰지 않는다(프로젝트 규칙)."""
    for model in MODELS:
        for column in model.__table__.columns:
            native = getattr(column.type, "native_enum", None)
            if native is not None:
                assert native is False, f"{model.__tablename__}.{column.name}"


def test_every_enum_column_has_a_check_constraint_repeating_its_values():
    """DB CHECK은 폭주만 받는 안전망이지만, 값 집합은 거기서도 닫혀 있어야 한다."""
    checks = {
        constraint.name: str(constraint.sqltext)
        for model in MODELS
        for constraint in model.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    for enum, name in (
        (KospiSlot, "ck_kospi_forecast_slot"),
        (KospiDirection, "ck_kospi_forecast_direction"),
        (KospiLlmRunKind, "ck_kospi_llm_run_kind"),
        (KospiLlmRunStatus, "ck_kospi_llm_run_status"),
        (KospiToolCallErrorKind, "ck_kospi_tool_call_error_kind"),
    ):
        text = checks[name]
        for member in enum:
            assert f"'{member.value}'" in text, f"{name}에 {member.value}가 없다"


def test_airflow_and_backend_agree_on_the_vocabulary():
    """**두 트리가 어긋나면 저장이 CHECK에서 죽는다.** Airflow는 `apps/`를 못 본다."""
    from modules.kospi.domain import Direction, RunSlot, ToolCallErrorKind

    assert [item.value for item in RunSlot] == [item.value for item in KospiSlot]
    assert [item.value for item in Direction] == [item.value for item in KospiDirection]
    assert [item.value for item in ToolCallErrorKind] == [item.value for item in KospiToolCallErrorKind]


def test_the_forecast_insert_names_only_real_columns():
    """수집기 테스트와 같은 이유다. 문자열 SQL이라 컬럼 이름이 틀려도 가짜 연결은 통과한다."""
    from modules.sql import read_sql

    named = _insert_columns(read_sql("postgres", "kospi_forecast", "insert.sql"))
    assert named <= set(KospiForecast.__table__.columns.keys())


def test_the_ledger_insert_names_only_real_columns():
    from modules.sql import read_sql

    for table, model in (("insert.sql", KospiLlmRun), ("insert_tool_call.sql", KospiToolCall)):
        named = _insert_columns(read_sql("postgres", "kospi_llm_run", table))
        assert named <= set(model.__table__.columns.keys()), table


def test_the_ledger_update_names_only_real_columns():
    from modules.sql import read_sql

    assigned = _assigned_columns(read_sql("postgres", "kospi_llm_run", "finish.sql"))
    assert assigned
    assert assigned <= set(KospiLlmRun.__table__.columns.keys())


def test_the_grade_update_names_only_real_columns():
    from modules.sql import read_sql

    assigned = _assigned_columns(read_sql("postgres", "kospi_forecast", "update_grade.sql"))
    assert assigned
    assert assigned <= set(KospiForecast.__table__.columns.keys())


def test_the_forecast_row_is_only_updated_by_grading():
    """전망은 첫 성공본 불변이다. 나중에 채워지는 것은 채점 넷과 `updated_at`뿐이다."""
    from modules.sql import read_sql

    assigned = _assigned_columns(read_sql("postgres", "kospi_forecast", "update_grade.sql"))
    # `updated_at = now()`는 자리표시자가 아니라 위 파서가 안 잡는다. 나머지 넷이 채점이다.
    assert assigned == {"actual_change_pct", "hit", "within_band", "graded_at"}


def test_grading_never_runs_twice_on_the_same_row():
    from modules.sql import read_sql

    statement = read_sql("postgres", "kospi_forecast", "update_grade.sql")
    assert "graded_at IS NULL" in statement


def test_the_forecast_insert_keeps_the_first_answer():
    from modules.sql import read_sql

    statement = read_sql("postgres", "kospi_forecast", "insert.sql")
    assert "ON CONFLICT ON CONSTRAINT uq_kospi_forecast_natural_key DO NOTHING" in statement
