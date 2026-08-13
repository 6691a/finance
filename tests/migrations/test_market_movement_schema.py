import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def test_market_movement_snapshot_is_created_with_its_natural_key(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE market_movement_snapshot" in sql
    assert "CONSTRAINT uq_market_movement_snapshot_natural_key UNIQUE (provider, symbol, observed_at)" in sql
    assert "symbol VARCHAR(20) NOT NULL" in sql


def test_market_movement_snapshot_constrains_its_values(capsys):
    sql = head_sql(capsys)

    assert "symbol IN ('KOSPI', 'KOSDAQ')" in sql
    # 종목 수는 음수가 될 수 없다. 다섯 값의 합계 제약은 두지 않는다.
    assert "upper_limit_count >= 0" in sql
    assert "lower_limit_count >= 0" in sql


def test_market_movement_snapshot_stores_the_five_counts_raw(capsys):
    sql = head_sql(capsys)

    statement = sql[sql.index("CREATE TABLE market_movement_snapshot") :]
    statement = statement[: statement.index(";")]
    for column in (
        "upper_limit_count",
        "rising_count",
        "unchanged_count",
        "falling_count",
        "lower_limit_count",
    ):
        assert f"{column} INTEGER NOT NULL" in statement
    # 비율과 3분류는 저장하지 않는다. 상승이 상한가를 포함하는지 아직 모른다.
    assert "ratio" not in statement
    assert "total" not in statement


def test_market_movement_snapshot_carries_comments(capsys):
    sql = head_sql(capsys)

    assert "COMMENT ON TABLE market_movement_snapshot IS" in sql
    for column in ("symbol", "observed_at", "rising_count", "upper_limit_count"):
        assert f"COMMENT ON COLUMN market_movement_snapshot.{column} IS" in sql
