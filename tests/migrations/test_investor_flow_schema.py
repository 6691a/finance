import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def test_the_stock_key_includes_the_update_slot(capsys):
    """한 응답에 슬롯마다 한 행이 온다. 슬롯이 키에 없으면 마지막 하나만 남는다."""
    sql = head_sql(capsys)

    assert "CREATE TABLE stock_investor_estimate_snapshot" in sql
    assert (
        "uq_stock_investor_estimate_snapshot_natural_key UNIQUE "
        "(provider, stock_code, business_date, source_time_code)" in sql
    )


def test_the_market_key_is_one_row_per_minute(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE market_investor_flow_snapshot" in sql
    assert "uq_market_investor_flow_snapshot_natural_key UNIQUE (provider, market_code, observed_at)" in sql


def test_the_market_check_covers_the_derivative_markets(capsys):
    """수집기 Enum이 보내는 일곱 시장을 제약이 전부 받아야 한다.

    이 대조는 `tests/collectors/test_kis_investor_flow.py`가 Enum 대 모델로 한 번 더 한다.
    여기서 보는 것은 마이그레이션이 실제 DDL을 그렇게 내는가다 — 모델만 고치고 리비전을
    빠뜨리면 저장이 CHECK 위반으로 죽는다.
    """
    sql = head_sql(capsys)

    # 넓히는 리비전이 CREATE 뒤에 오므로 마지막에 남는 제약을 본다.
    widened = "market_code IN ('KOSPI', 'KOSDAQ', 'FUTURES', 'CALL_OPTION', 'PUT_OPTION', 'STOCK_FUTURES', 'ETF')"
    assert widened in sql
    assert sql.index(widened) > sql.index("CREATE TABLE market_investor_flow_snapshot")


def test_market_flow_stores_all_three_investor_groups(capsys):
    sql = head_sql(capsys)

    statement = sql[sql.index("CREATE TABLE market_investor_flow_snapshot") :]
    statement = statement[: statement.index(";")]
    for group in ("foreign", "institution", "individual"):
        for suffix in ("sell_qty", "buy_qty", "net_buy_qty", "net_buy_amount"):
            assert f"{group}_{suffix}" in statement


def test_no_delta_column_is_created(capsys):
    """누적값이라 델타를 저장하지 않는다. 조회에서 lag()로 계산한다."""
    sql = head_sql(capsys)

    statement = sql[sql.index("CREATE TABLE market_investor_flow_snapshot") :]
    statement = statement[: statement.index(";")]
    assert "delta" not in statement


def test_both_tables_restrict_the_lineage_delete(capsys):
    sql = head_sql(capsys)

    for table in ("stock_investor_estimate_snapshot", "market_investor_flow_snapshot"):
        statement = sql[sql.index(f"CREATE TABLE {table}") :]
        statement = statement[: statement.index(";")]
        assert statement.count("REFERENCES source_record (id) ON DELETE RESTRICT") == 1
        assert f"CREATE INDEX ix_{table}_source_record_id" in sql
        assert f"COMMENT ON TABLE {table} IS" in sql


def test_the_member_flow_tables_are_not_created_yet(capsys):
    """회원사 테이블은 상주 WebSocket 서비스와 함께 만든다."""
    sql = head_sql(capsys)

    assert "foreign_member_flow_snapshot" not in sql
