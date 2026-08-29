import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def test_disclosure_event_is_created_with_its_natural_key(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE disclosure_event" in sql
    assert "CONSTRAINT uq_disclosure_event_natural_key UNIQUE (provider, rcept_no)" in sql
    assert "detected_at TIMESTAMP WITH TIME ZONE NOT NULL" in sql


def test_disclosure_event_restricts_the_lineage_delete(capsys):
    sql = head_sql(capsys)

    statement = sql[sql.index("CREATE TABLE disclosure_event") :]
    statement = statement[: statement.index(";")]
    assert statement.count("REFERENCES source_record (id) ON DELETE RESTRICT") == 1


def test_the_minute_level_receipt_time_is_gone(capsys):
    """공식 RSS로는 과거를 채울 수 없어 컬럼을 두지 않는다.

    `disclosure_event` 안만 본다. 문서 아카이브의 `document`에는 제공처가 알려 준
    `published_at`이 있고 그건 이 규칙과 무관하다.
    """
    sql = head_sql(capsys)

    statement = sql[sql.index("CREATE TABLE disclosure_event") :]
    statement = statement[: statement.index(";")]
    assert "published_at" not in statement


def test_disclosure_event_carries_the_body_column(capsys):
    """목록 API는 보고서명까지만 준다. 인과 그래프가 그 한 줄로 사건을 만들려다 내용을
    지어냈다(2026-08-28) — 원문에서 태그를 걷어낸 문장을 여기 담는다.

    **nullable이다.** 시장이 반응하는 종류에만 채우고, 아직 못 받은 것과 대상이 아닌 것이
    둘 다 NULL이다.
    """
    sql = head_sql(capsys)

    assert "ALTER TABLE disclosure_event ADD COLUMN body TEXT" in sql
    assert "COMMENT ON COLUMN disclosure_event.body IS" in sql


def test_earnings_fact_is_created_with_its_five_part_key(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE earnings_fact" in sql
    assert (
        "CONSTRAINT uq_earnings_fact_natural_key UNIQUE (provider, rcept_no, statement_scope, amount_basis, metric)"
        in sql
    )


def test_earnings_fact_constrains_the_closed_value_sets(capsys):
    sql = head_sql(capsys)

    assert "release_type IN ('provisional', 'periodic')" in sql
    assert "statement_scope IN ('CFS', 'OFS')" in sql
    assert "amount_basis IN ('period', 'cumulative')" in sql
    assert "metric IN ('revenue', 'operating_profit', 'net_income')" in sql


def test_earnings_fact_does_not_reference_disclosure_event(capsys):
    sql = head_sql(capsys)

    statement = sql[sql.index("CREATE TABLE earnings_fact") :]
    statement = statement[: statement.index(";")]
    # 외래키를 걸면 원문 파싱 실패가 공시 이벤트 수집까지 막는다. rcept_no로만 잇는다.
    assert "disclosure_event" not in statement


def test_dart_tables_carry_comments(capsys):
    sql = head_sql(capsys)

    for table in ("disclosure_event", "earnings_fact"):
        assert f"COMMENT ON TABLE {table} IS" in sql
    for column in ("rcept_no", "receipt_date", "detected_at"):
        assert f"COMMENT ON COLUMN disclosure_event.{column} IS" in sql
    for column in ("metric", "amount_basis", "current_amount", "prior_year_amount"):
        assert f"COMMENT ON COLUMN earnings_fact.{column} IS" in sql
