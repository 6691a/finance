"""주간 인과 그래프 테이블이 오프라인 SQL에 서는지.

계약은 docs/analysis/market-causal-graph.md §3·§10.1이다. 리비전 ID에 고정하지 않고
테이블 단위 사실만 본다 — 리비전을 다시 만들 때마다 깨지지 않게.
"""

import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def test_the_four_causal_tables_are_created(capsys):
    sql = head_sql(capsys)

    for table in (
        "market_event",
        "market_channel",
        "market_causal_path",
        "market_causal_step",
    ):
        assert f"CREATE TABLE {table}" in sql


def test_the_masters_carry_their_natural_keys(capsys):
    """같은 제목이 다른 날 다시 일어나면 다른 사건이고, 채널은 이름 하나가 키다."""
    sql = head_sql(capsys)

    assert (
        "CONSTRAINT uq_market_event_natural_key UNIQUE (title, occurred_on)" in sql
    )
    assert "CONSTRAINT uq_market_channel_natural_key UNIQUE (name)" in sql


def test_the_path_key_carries_the_chain(capsys):
    """`chain_key`가 빠지면 같은 사건이 같은 대상에 낸 두 번째 경로가 조용히 삼켜진다."""
    sql = head_sql(capsys)

    assert (
        "CONSTRAINT uq_market_causal_path_natural_key UNIQUE "
        "(week_start, event_id, target_kind, target_code, chain_key)" in sql
    )


def test_causal_enum_values_are_constrained(capsys):
    sql = head_sql(capsys)

    assert "sign IN ('up', 'down')" in sql
    assert "confidence IN ('observed', 'plausible')" in sql
    assert "target_kind IN ('instrument', 'index', 'quote', 'indicator')" in sql


def test_step_position_is_bounded_and_ordered(capsys):
    sql = head_sql(capsys)

    assert (
        "CONSTRAINT uq_market_causal_step_natural_key UNIQUE (path_id, position)" in sql
    )
    assert "position BETWEEN 1 AND 3" in sql


def test_the_llm_ledger_accepts_the_causal_kind(capsys):
    """원장을 나누지 않고 종류를 하나 더한다(설계 §3.5)."""
    sql = head_sql(capsys)

    assert "'forecast', 'review', 'nxt_review', 'narration', 'causal'" in sql


def test_the_llm_ledger_slot_becomes_optional_only_for_causal(capsys):
    """주간 분석에는 슬롯이 없다. 나머지 종류가 슬롯을 빠뜨리는 것은 그대로 막는다."""
    sql = head_sql(capsys)

    assert "ALTER TABLE thesis_llm_run ALTER COLUMN run_slot DROP NOT NULL" in sql
    assert "ck_thesis_llm_run_slot_shape" in sql
