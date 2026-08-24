"""매매 신호 테이블 리비전의 테이블 단위 사실.

특정 리비전 ID나 전체 문자열에 고정하지 않는다. offline SQL에 다음이 있으면 충분하다:
테이블, 멱등키 UNIQUE, 값 집합 CHECK, 결측 허용 칸, 주석.
"""

import pytest

from apps.models.analysis import TechnicalSignal, TechnicalSignalKind
from modules.technical import SignalKind
from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def _table_statement(sql: str, table: str) -> str:
    statement = sql[sql.index(f"CREATE TABLE {table} (") :]
    return statement[: statement.index(";")]


def test_the_table_is_created_without_a_schema_prefix(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE technical_signal (" in sql
    assert "CREATE SCHEMA" not in sql
    assert "CREATE TABLE analysis." not in sql


def test_one_row_per_subject_day_and_kind(capsys):
    """같은 날 같은 종류의 사건은 하나다. 매일 다시 검출해도 행이 늘지 않는다."""
    statement = _table_statement(head_sql(capsys), "technical_signal")

    assert "CONSTRAINT uq_technical_signal_natural_key UNIQUE (provider, symbol, signal_date, kind)" in statement


def test_the_closed_value_sets_are_constrained(capsys):
    statement = _table_statement(head_sql(capsys), "technical_signal")

    assert (
        "CONSTRAINT ck_technical_signal_kind CHECK (kind IN ('sma_cross', 'macd_cross', 'rsi_reversal'))" in statement
    )
    # 신호에 flat은 없다. 교차는 위 아니면 아래다.
    assert "CONSTRAINT ck_technical_signal_direction CHECK (direction IN ('up', 'down'))" in statement


def test_only_the_volume_ratio_may_be_missing(capsys):
    """거래량을 못 세는 날은 있어도 지표를 못 내는 사건은 없다."""
    statement = _table_statement(head_sql(capsys), "technical_signal")

    for column in ("close", "sma20", "sma60", "rsi14", "macd", "macd_signal", "rule_version"):
        assert f"{column} " in statement
    assert "volume_ratio20 NUMERIC(10, 4)," in statement
    assert "volume_ratio20 NUMERIC(10, 4) NOT NULL" not in statement


def test_the_table_and_columns_carry_comments(capsys):
    sql = head_sql(capsys)

    assert "COMMENT ON TABLE technical_signal IS" in sql
    for column in ("kind", "direction", "signal_date", "rule_version", "volume_ratio20"):
        assert f"COMMENT ON COLUMN technical_signal.{column} IS" in sql


def test_the_model_enum_matches_the_calculator():
    """저장 값과 검출 값이 어긋나면 CHECK가 런타임에 터진다."""
    assert {member.value for member in TechnicalSignalKind} == {member.value for member in SignalKind}


def test_signals_can_be_cited_as_evidence(capsys):
    """지표는 문맥이라 인용 대상이 아니지만 신호는 행 ID를 가진 사건이다(문서 14.3절)."""
    sql = head_sql(capsys)

    assert "evidence_kind IN ('document', 'disclosure', 'macro_change', 'technical_signal')" in sql


def test_the_model_does_not_link_a_source_record():
    """외부 응답이 아니라 파생 사건이다. 계보는 원천 일봉이 갖는다."""
    assert "source_record_id" not in TechnicalSignal.__table__.columns
