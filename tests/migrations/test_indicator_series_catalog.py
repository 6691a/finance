import pytest

from modules.collectors.boe import GiltSeries
from modules.collectors.ecb import EuroYieldSeries
from modules.collectors.ecos import MarketRateSeries
from modules.collectors.fred import TREASURY_SERIES
from modules.collectors.mof import JgbSeries
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


@pytest.mark.parametrize("series", list(JgbSeries))
def test_every_mof_series_has_a_master_row(series, capsys):
    sql = head_sql(capsys)

    assert f"'mof', '{series.value}'" in sql


@pytest.mark.parametrize("series", list(GiltSeries))
def test_every_boe_series_has_a_master_row(series, capsys):
    sql = head_sql(capsys)

    assert f"'boe', '{series.value}'" in sql


@pytest.mark.parametrize("series", list(EuroYieldSeries))
def test_every_ecb_series_has_a_master_row(series, capsys):
    sql = head_sql(capsys)

    assert f"'ecb', '{series.value}'" in sql


@pytest.mark.parametrize(
    ("provider", "series", "country", "country_name"),
    [
        *[("mof", series, "JP", "일본") for series in JgbSeries],
        *[("boe", series, "GB", "영국") for series in GiltSeries],
        # 유로 지역은 나라가 아니라 통화권이다. ISO 국가 코드가 아니라 XM이 들어간다.
        *[("ecb", series, "XM", "유로 지역") for series in EuroYieldSeries],
    ],
)
def test_the_master_row_keeps_the_maturity_the_collector_declares(provider, series, country, country_name, capsys):
    # 만기가 어긋나면 국가 비교 패널이 다른 만기를 같은 줄에 그린다. DAG는 죽지 않는다.
    sql = head_sql(capsys)

    assert (
        f"'{provider}', '{series.value}', '{country}', '{country_name}', {series.maturity_months}, 'government_bond'"
    ) in sql
