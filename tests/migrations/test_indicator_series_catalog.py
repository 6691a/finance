import pytest

from modules.collectors.ecos import MarketRateSeries
from modules.collectors.fred import TREASURY_SERIES
from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def test_indicator_series_master_describes_country_and_maturity(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE indicator_series" in sql
    assert "country TEXT NOT NULL" in sql
    assert "maturity_months INTEGER NOT NULL" in sql
    assert "CONSTRAINT uq_indicator_series_natural_key UNIQUE (provider, series_id)" in sql
    assert "CONSTRAINT ck_indicator_series_kind CHECK (kind IN ('government_bond', 'money_market'))" in sql


@pytest.mark.parametrize("series_id", TREASURY_SERIES)
def test_every_fred_series_has_a_master_row(series_id, capsys):
    # 관측값에서 마스터로 외래키를 걸지 않는다. 수집기 Enum에만 추가하고 마스터를 빠뜨리면
    # 통합 대시보드에서 그 시계열만 조용히 사라지므로 여기서 대조한다.
    sql = head_sql(capsys)

    assert f"'fred', '{series_id}'" in sql


@pytest.mark.parametrize("series", list(MarketRateSeries))
def test_every_ecos_series_has_a_master_row(series, capsys):
    sql = head_sql(capsys)

    assert f"'ecos', '{series.value}'" in sql
