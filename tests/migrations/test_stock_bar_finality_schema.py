"""stock_bar에 ingest_method/is_final을 더한 리비전의 테이블 단위 사실.

특정 리비전 ID나 전체 문자열에 고정하지 않는다. offline SQL에 다음이 있으면 충분하다:
두 컬럼 추가, 기존 행 백필용 default와 그 제거, ingest_method CHECK.
"""

import pytest

from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def test_stock_bar_gains_ingest_method_and_finality(capsys):
    sql = head_sql(capsys)

    assert "ALTER TABLE stock_bar ADD COLUMN ingest_method" in sql
    assert "ALTER TABLE stock_bar ADD COLUMN is_final" in sql


def test_existing_rows_are_backfilled_as_rest_then_the_default_is_dropped(capsys):
    sql = head_sql(capsys)

    # 기존 행은 전부 REST 산출이라 'rest'/true로 백필한다.
    assert "DEFAULT 'rest'" in sql
    assert "DEFAULT true" in sql
    # default를 남기면 이 리비전을 모르는 INSERT가 조용히 기본값으로 저장된다.
    assert "ALTER COLUMN ingest_method DROP DEFAULT" in sql
    assert "ALTER COLUMN is_final DROP DEFAULT" in sql


def test_ingest_method_values_are_constrained(capsys):
    sql = head_sql(capsys)

    assert "ck_stock_bar_ingest_method" in sql
    assert "ingest_method IN ('websocket', 'rest')" in sql
