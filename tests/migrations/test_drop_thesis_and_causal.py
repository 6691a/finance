"""옛 시장 추론·주간 인과 그래프 표 열둘의 삭제.

리비전 ID에 고정하지 않는다 — 테이블 단위 사실만 본다(`tests/migrations`의 규칙).
"""

import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)

DROPPED = {
    "thesis",
    "thesis_outcome",
    "thesis_evidence",
    "thesis_precedent",
    "thesis_tool_call",
    "thesis_llm_run",
    "market_event",
    "market_channel",
    "market_causal_path",
    "market_causal_step",
    "market_causal_evidence",
    "market_causal_direction",
}


def test_the_old_tables_are_dropped_child_first(capsys):
    """**PostgreSQL이 참조되는 표의 DROP을 거절한다.** 순서가 곧 정확성이다.

    `thesis_llm_run`이 마지막이다 — 추론과 인과 양쪽이 그것을 참조했다.
    """
    sql = head_sql(capsys)
    order = [line.split()[2].rstrip(";") for line in sql.splitlines() if line.startswith("DROP TABLE ")]

    def before(child: str, parent: str) -> bool:
        return order.index(child) < order.index(parent)

    assert before("market_causal_step", "market_causal_path")
    assert before("market_causal_step", "market_channel")
    assert before("market_causal_evidence", "market_causal_path")
    assert before("market_causal_path", "market_event")
    assert before("market_causal_path", "thesis_llm_run")
    assert before("market_causal_direction", "thesis_llm_run")
    for child in ("thesis_outcome", "thesis_evidence", "thesis_precedent"):
        assert before(child, "thesis")
    assert before("thesis_tool_call", "thesis_llm_run")
    assert before("thesis", "thesis_llm_run")

    # 열둘 전부 지운다. 하나라도 빠지면 데이터를 든 표가 소유자 없이 남는다.
    assert DROPPED <= set(order)


def test_no_model_still_declares_a_dropped_table():
    """모델에 남아 있으면 다음 autogenerate가 그 표를 **다시 만든다.**"""
    import apps.models  # noqa: F401  등록 부수효과
    from apps.core.database import Base

    assert DROPPED & set(Base.metadata.tables) == set()


def test_the_signal_rule_version_comment_no_longer_points_at_thesis(capsys):
    """모델과 마이그레이션의 주석이 어긋나면 autogenerate가 매번 `COMMENT ON` 차이를 낸다."""
    from apps.models.analysis import TechnicalSignal

    comment = TechnicalSignal.__table__.c.rule_version.comment

    assert comment is not None
    assert "thesis" not in comment
    assert f"IS '{comment}'" in head_sql(capsys)
