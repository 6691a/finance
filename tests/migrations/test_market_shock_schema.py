import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def test_market_shock_event_is_created_with_its_natural_key(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE market_shock_event" in sql
    assert "CONSTRAINT uq_market_shock_event_natural_key UNIQUE (symbol, detected_at)" in sql


def test_market_shock_event_constrains_its_enum_values(capsys):
    sql = head_sql(capsys)

    assert "direction IN ('drop', 'surge')" in sql
    assert "cause_status IN ('pending', 'resolved', 'unknown')" in sql
    assert "cause_kind IS NULL OR cause_kind IN ('rumor', 'confirmed', 'unclear')" in sql


def test_market_shock_event_opens_pending_with_no_attempts(capsys):
    """포착은 원인 없이 열린다. 원인 DAG가 나중에 채운다."""
    sql = head_sql(capsys)
    statement = sql[sql.index("CREATE TABLE market_shock_event") :]
    statement = statement[: statement.index(";")]

    assert "cause_status VARCHAR(20) DEFAULT 'pending' NOT NULL" in statement
    assert "cause_attempts INTEGER DEFAULT '0' NOT NULL" in statement
    assert "cause_weak BOOLEAN DEFAULT 'false' NOT NULL" in statement
    # 원인 칸은 전부 나중에 채워지므로 NULL을 받는다.
    assert "cause_text TEXT" in statement
    assert "cause_resolved_at TIMESTAMP WITH TIME ZONE" in statement


def test_market_shock_event_keeps_the_threshold_that_made_the_row(capsys):
    """손잡이를 옮긴 뒤 옛 행과 섞이지 않게 임계가 행에 남는다."""
    sql = head_sql(capsys)
    statement = sql[sql.index("CREATE TABLE market_shock_event") :]
    statement = statement[: statement.index(";")]

    assert "threshold_pct NUMERIC(10, 4) NOT NULL" in statement
    assert "window_start" in statement
    assert "window_end" in statement


def test_market_shock_event_indexes_the_pending_cause_lookup(capsys):
    """원인 DAG가 매일 아침 이 조건으로 대상을 고른다."""
    sql = head_sql(capsys)

    assert "ix_market_shock_event_cause_pending" in sql
    assert "cause_status, cause_deadline" in sql


def test_market_shock_search_hit_is_created_with_its_natural_key(capsys):
    """같은 기사가 여러 질의에서 나와도 한 행이다."""
    sql = head_sql(capsys)

    assert "CREATE TABLE market_shock_search_hit" in sql
    assert "CONSTRAINT uq_market_shock_search_hit_natural_key UNIQUE (shock_event_id, url)" in sql
    assert "FOREIGN KEY(shock_event_id) REFERENCES market_shock_event (id) ON DELETE CASCADE" in sql


def test_market_shock_search_hit_keeps_the_snapshot_we_saw(capsys):
    """밖의 페이지는 바뀌고 사라진다. 우리가 본 것이 이 행이다."""
    sql = head_sql(capsys)
    statement = sql[sql.index("CREATE TABLE market_shock_search_hit") :]
    statement = statement[: statement.index(";")]

    for column in ("query TEXT NOT NULL", "snippet TEXT NOT NULL", "url TEXT NOT NULL"):
        assert column in statement
    assert "retrieved_at TIMESTAMP WITH TIME ZONE NOT NULL" in statement
    # 제공처가 매긴 관련도는 받은 값 그대로 둔다. 없을 수 있어 nullable이다.
    assert "relevance NUMERIC(6, 4)" in statement
    assert "relevance IS NULL OR (relevance >= 0 AND relevance <= 1)" in sql


def test_the_event_records_whether_search_solved_it(capsys):
    """우리 문서만으로 푼 건과 갈라야 '검색이 몇 %를 풀었나'를 셀 수 있다."""
    sql = head_sql(capsys)

    assert "cause_search_used" in sql
