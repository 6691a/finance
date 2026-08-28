import pytest

from modules.collectors.indicator.bbk import BundSeries
from modules.collectors.indicator.boe import GILT_DATASET, BoeSeries
from modules.collectors.indicator.boe import POLICY_RATE_SERIES as BOE_POLICY_SERIES
from modules.collectors.indicator.ecb import EuroYieldSeries
from modules.collectors.indicator.ecb_irs import MATURITY_MONTHS as CONVERGENCE_MATURITY_MONTHS
from modules.collectors.indicator.ecb_irs import ConvergenceSeries
from modules.collectors.indicator.ecos import POLICY_RATE_SERIES as ECOS_POLICY_SERIES
from modules.collectors.indicator.ecos import EcosSeries
from modules.collectors.indicator.fred import MACRO_SERIES, SIGNAL_SERIES, TREASURY_SERIES
from modules.collectors.indicator.fred import POLICY_RATE_SERIES as FRED_POLICY_SERIES
from modules.collectors.indicator.kcs import ALL_SERIES as KCS_SERIES
from modules.collectors.indicator.mof import JgbSeries
from tests.helpers import NO_REVISION_REASON, head_sql, revision_files

pytestmark = pytest.mark.skipif(not revision_files(), reason=NO_REVISION_REASON)


def test_indicator_series_master_describes_country_and_maturity(capsys):
    sql = head_sql(capsys)

    assert "CREATE TABLE indicator_series" in sql
    assert "country TEXT NOT NULL" in sql
    assert "CONSTRAINT uq_indicator_series_natural_key UNIQUE (provider, series_id)" in sql


def test_the_master_takes_series_without_a_maturity(capsys):
    """물가지수에는 만기가 없다. 0으로 채우면 만기별 비교 쿼리가 "0개월물"로 그린다.

    **리비전 이력이 아니라 마지막 상태를 본다.** `head --sql`은 테이블을 만든 뒤 제약을 갈아
    끼우는 문장을 순서대로 다 찍으므로, 앞에 나오는 옛 정의만 보면 지금 무엇이 걸려 있는지
    알 수 없다.
    """
    sql = head_sql(capsys)

    assert sql.rindex("maturity_months IS NULL OR maturity_months > 0") > sql.rindex("CREATE TABLE indicator_series")
    assert "ALTER TABLE indicator_series ALTER COLUMN maturity_months DROP NOT NULL" in sql


def test_the_master_takes_kinds_that_are_not_interest_rates(capsys):
    sql = head_sql(capsys)

    latest = sql.rindex("ck_indicator_series_kind")
    assert "'price_index'" in sql[latest : latest + 200]
    assert "'activity'" in sql[latest : latest + 200]


def test_the_master_tells_policy_rates_from_market_rates(capsys):
    """중앙은행이 정하는 값은 `money_market`이 아니다.

    한 축에 섞으면 시장금리 패널이 정책금리 계단을 함께 그린다. 조회하는 쪽이 `kind`를
    반드시 걸게 돼 있으므로 새 값이어야 한다.
    """
    sql = head_sql(capsys)

    latest = sql.rindex("ck_indicator_series_kind")
    assert "'policy_rate'" in sql[latest : latest + 200]


@pytest.mark.parametrize(
    ("provider", "series_id"),
    [
        *[("ecos", series_id) for series_id in ECOS_POLICY_SERIES],
        *[("fred", series_id) for series_id in FRED_POLICY_SERIES],
        *[("boe", series_id) for series_id in BOE_POLICY_SERIES],
    ],
)
def test_every_policy_rate_series_has_a_master_row_without_a_maturity(provider, series_id, capsys):
    # 정책금리에는 만기가 없다. 0으로 채우면 만기별 비교 쿼리가 그 시계열을 "0개월물"로 그린다.
    sql = head_sql(capsys)

    assert f"'{provider}', '{series_id}'" in sql
    row_start = sql.index(f"'{provider}', '{series_id}'")
    assert "NULL, 'policy_rate'" in sql[row_start : row_start + 200]


@pytest.mark.parametrize("series_id", TREASURY_SERIES)
def test_every_fred_series_has_a_master_row(series_id, capsys):
    # 관측값에서 마스터로 외래키를 걸지 않는다. 수집기 Enum에만 추가하고 마스터를 빠뜨리면
    # 통합 대시보드에서 그 시계열만 조용히 사라지므로 여기서 대조한다.
    sql = head_sql(capsys)

    assert f"'fred', '{series_id}'" in sql


@pytest.mark.parametrize("series_id", MACRO_SERIES)
def test_every_macro_series_has_a_master_row(series_id, capsys):
    sql = head_sql(capsys)

    assert f"'fred', '{series_id}'" in sql


@pytest.mark.parametrize("series_id", SIGNAL_SERIES)
def test_every_signal_series_has_a_master_row(series_id, capsys):
    sql = head_sql(capsys)

    assert f"'fred', '{series_id}'" in sql


def test_the_master_separates_real_rates_and_credit_from_the_curve(capsys):
    """실질금리는 명목 국채와 만기가 같다. 같은 `kind`면 미국 10년물이 두 개로 보인다.

    신용스프레드는 국채가 아니라 회사채 초과수익이라 곡선에 얹으면 만기 축이 거짓이 된다.
    """
    sql = head_sql(capsys)

    latest = sql.rindex("ck_indicator_series_kind")
    assert "'tips_rate'" in sql[latest : latest + 250]
    assert "'credit_spread'" in sql[latest : latest + 250]


def test_the_tips_pair_keeps_the_nominal_maturity(capsys):
    # 만기를 NULL로 두면 명목 10년물과 나란히 놓고 볼 수 없다. 둘을 더하면 그 명목 금리다.
    sql = head_sql(capsys)

    for series_id in ("REAL10Y", "BREAKEVEN10Y"):
        row_start = sql.index(f"'fred', '{series_id}'")
        assert "120, 'tips_rate'" in sql[row_start : row_start + 200]


@pytest.mark.parametrize("series_id", KCS_SERIES)
def test_every_korea_trade_series_has_a_master_row(series_id, capsys):
    sql = head_sql(capsys)

    assert f"'kcs', '{series_id}'" in sql


@pytest.mark.parametrize("series_id", ["KR_EXPORT_SEMICON_MTD", "KR_IMPORT_CHIPEQUIP_MTD", "KR_EXPORT_CN_MTD"])
def test_korea_trade_rows_have_no_maturity(series_id, capsys):
    # 수출입에는 만기 개념이 없다. 0으로 채우면 만기별 비교 쿼리가 "0개월물"로 그린다.
    sql = head_sql(capsys)

    row_start = sql.index(f"'kcs', '{series_id}'")
    assert "NULL, 'activity'" in sql[row_start : row_start + 200]


def test_country_rows_stay_korean_indicators(capsys):
    """대중국 수출은 한국의 지표다. `country`가 상대국이 되면 중국 지표로 잡힌다."""
    sql = head_sql(capsys)

    row_start = sql.index("'kcs', 'KR_EXPORT_CN_MTD'")
    assert "'KR', '대한민국'" in sql[row_start : row_start + 200]


@pytest.mark.parametrize("series", list(EcosSeries))
def test_every_ecos_series_has_a_master_row(series, capsys):
    sql = head_sql(capsys)

    assert f"'ecos', '{series.value}'" in sql


@pytest.mark.parametrize("series", list(JgbSeries))
def test_every_mof_series_has_a_master_row(series, capsys):
    sql = head_sql(capsys)

    assert f"'mof', '{series.value}'" in sql


@pytest.mark.parametrize("series", list(BoeSeries))
def test_every_boe_series_has_a_master_row(series, capsys):
    sql = head_sql(capsys)

    assert f"'boe', '{series.value}'" in sql


@pytest.mark.parametrize("series", list(EuroYieldSeries))
def test_every_ecb_series_has_a_master_row(series, capsys):
    sql = head_sql(capsys)

    assert f"'ecb', '{series.value}'" in sql


@pytest.mark.parametrize("series", list(BundSeries))
def test_every_bbk_series_has_a_master_row(series, capsys):
    sql = head_sql(capsys)

    assert f"'bbk', '{series.value}'" in sql


@pytest.mark.parametrize("series", list(ConvergenceSeries))
def test_every_ecb_convergence_series_has_a_master_row(series, capsys):
    # `ecb_irs`는 `ecb.py`와 provider를 공유한다. 같은 `ecb` 아래 있어도 시드는 따로 넣으므로
    # 여기서 따로 대조해야 한다.
    sql = head_sql(capsys)

    assert f"'ecb', '{series.value}'" in sql


@pytest.mark.parametrize(
    ("provider", "series_id", "country", "country_name", "maturity_months"),
    [
        *[("mof", series.value, "JP", "일본", series.maturity_months) for series in JgbSeries],
        *[("boe", series.value, "GB", "영국", series.maturity_months) for series in GILT_DATASET.series],
        # 유로 지역은 나라가 아니라 통화권이다. ISO 국가 코드가 아니라 XM이 들어간다.
        *[("ecb", series.value, "XM", "유로 지역", series.maturity_months) for series in EuroYieldSeries],
        *[("bbk", series.value, "DE", "독일", series.maturity_months) for series in BundSeries],
        # 수렴 기준 금리는 나라마다 잔존 10년 국채 하나뿐이라 만기가 수집기 상수다.
        *[
            ("ecb", series.value, series.country, series.country_name, CONVERGENCE_MATURITY_MONTHS)
            for series in ConvergenceSeries
        ],
    ],
)
def test_the_master_row_keeps_the_maturity_the_collector_declares(
    provider, series_id, country, country_name, maturity_months, capsys
):
    # 만기가 어긋나면 국가 비교 패널이 다른 만기를 같은 줄에 그린다. DAG는 죽지 않는다.
    sql = head_sql(capsys)

    assert (f"'{provider}', '{series_id}', '{country}', '{country_name}', {maturity_months}, 'government_bond'") in sql
