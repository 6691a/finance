"""DAG 객체와 실패 판정만 검증한다.

수집·검증 규칙은 `modules/collectors/indicator/`의 `fred.py`·`ecos.py`·`bbk_statement.py`·
`boe.py`에 있고 `tests/collectors/`가 덮는다. 설계는
docs/collection/central-bank-assets-collection.md다.
"""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import central_bank_assets_weekly
from modules.collectors.indicator.bbk_statement import BALANCE_SHEET_SERIES as BBK_ASSET_SERIES
from modules.collectors.indicator.boe import BALANCE_SHEET_DATASET
from modules.collectors.indicator.boe import BALANCE_SHEET_SERIES as BOE_ASSET_SERIES
from modules.collectors.indicator.ecos import BALANCE_SHEET_SERIES as ECOS_ASSET_SERIES
from modules.collectors.indicator.fred import BALANCE_SHEET_SERIES as FRED_ASSET_SERIES

DAG = central_bank_assets_weekly.central_bank_assets_weekly


def test_the_dag_runs_once_a_week_after_the_weekend():
    # KST 월 09:20 = UTC 일 00:20. 그 시점이면 지난주 발표가 전부 끝나 있다.
    assert DAG.schedule == "20 9 * * 1"
    assert DAG.max_active_runs == 1


def test_it_does_not_collide_with_the_policy_rate_dag():
    """같은 요일에 돌지만 시각을 벌린다. 둘 다 ECOS·FRED를 부른다."""
    from dags import policy_rate_weekly

    assert DAG.schedule != policy_rate_weekly.policy_rate_weekly.schedule


def test_one_task_per_provider_so_one_failure_does_not_take_the_others():
    # 제공처가 넷이라 태스크도 넷이다. 하나가 실패해도 나머지는 저장되고 재시도도 그것만 돈다.
    assert set(DAG.task_dict) == {"collect_fred", "collect_ecos", "collect_bbk", "collect_boe"}

    # 태스크 사이에 의존이 없어야 앞의 실패가 뒤를 skip으로 만들지 않는다.
    for task in DAG.task_dict.values():
        assert not task.upstream_task_ids


def test_the_window_reaches_past_the_slowest_publication_lag():
    """창 하나가 제공처 넷을 전부 덮어야 한다.

    2026-08-28에 실제로 호출해 확인한 지연이 둘이다. BoE 총자산은 분기 고시에 최신값이
    2025-03-31, 한국은행 총자산은 월간에 최신값이 2026-06이다. 45일 창에는 둘 다 행이 한 줄도
    안 잡히고, IADB는 그때 HTML 오류 페이지를 주고 ECOS는 조용한 0건을 준다.
    """
    window = central_bank_assets_weekly.LOOKBACK_DAYS_ASSETS

    assert window == DAG.params["lookback_days"]
    assert window > (date(2026, 8, 27) - date(2025, 3, 31)).days
    assert window > (date(2026, 8, 27) - date(2026, 6, 1)).days
    # 분기 경계가 여럿 들어가야 한 분기 고시가 밀려도 빈 응답이 되지 않는다.
    assert window >= 2 * 365


def test_every_task_shares_that_one_window():
    """제공처마다 창을 다르게 두지 않는다. 하나가 가장 밀린 계열을 덮으면 나머지는 덤이다."""
    source = Path(central_bank_assets_weekly.__file__).read_text()

    assert source.count("resolve_period()") == 1 + len(DAG.task_dict)
    assert "MIN_LOOKBACK" not in source


def test_zero_observations_kill_the_task():
    """800일 창에서 0건은 발표 전이 아니라 제공처나 식별자가 바뀐 것이다.

    ECOS는 그 상태를 데이터 없음(`INFO-200`)으로 답해 예외를 내지 않는다. 여기서 세지 않으면
    조용한 성공이 된다 — 2026-08-28에 `KRASSETS_M`이 실제로 그렇게 0건으로 돌아왔다.
    """
    with pytest.raises(AirflowFailException, match="KRASSETS_M returned no observations"):
        central_bank_assets_weekly.require_observations(
            "KRASSETS_M", 0, date(2026, 6, 20), date(2026, 8, 28)
        )


def test_a_non_empty_result_passes():
    assert central_bank_assets_weekly.require_observations("KRASSETS_M", 1, date(2026, 6, 20), date(2026, 8, 28)) is None


def test_the_display_metadata_is_filled():
    assert DAG.dag_display_name
    assert DAG.description
    assert DAG.doc_md
    for param in DAG.params.values():
        assert param.schema.get("title")
        assert param.description


def test_the_start_date_is_a_kst_midnight():
    start = DAG.start_date

    assert start.tzinfo is not None
    # KST 2026-08-31 00:00 = UTC 2026-08-30 15:00.
    assert start.astimezone(UTC) == datetime(2026, 8, 30, 15, 0, tzinfo=UTC)


def test_the_six_central_banks_are_split_across_the_four_providers():
    collected = set(FRED_ASSET_SERIES) | set(ECOS_ASSET_SERIES) | set(BBK_ASSET_SERIES) | set(BOE_ASSET_SERIES)

    assert collected == {
        "FEDASSETS_W",
        "EAASSETS_W",
        "JPASSETS_M",
        "KRASSETS_M",
        "DEASSETS_W",
        "GBASSETS_Q",
        "GBRESERVES_W",
    }
    # 같은 IADB를 국채 DAG과 정책금리 DAG도 부른다. source_key가 갈려야 어느 묶음을 받았는지 되짚는다.
    assert BALANCE_SHEET_DATASET.source_key == "bank_balance_sheet"


def test_one_failed_series_kills_the_task():
    """주 1회라 다음 실행이 곧 같은 창을 다시 보지 않는다(한 주 뒤다).

    사유에 쉼표가 들어가므로 구분자는 `;`다.
    """
    with pytest.raises(AirflowFailException, match="FEDASSETS_W.*; .*JPASSETS_M"):
        central_bank_assets_weekly.require_no_failures("FRED", ["FEDASSETS_W(boom, again)", "JPASSETS_M(boom)"])


def test_no_failure_lets_the_task_succeed():
    assert central_bank_assets_weekly.require_no_failures("FRED", []) is None


@pytest.mark.parametrize("code", ["INFO-100", "ERROR-100", "ERROR-300"])
def test_unrecoverable_ecos_codes_are_not_retried(code):
    assert central_bank_assets_weekly.is_unrecoverable_result(code)


@pytest.mark.parametrize("code", ["INFO-200", "ERROR-500", "ERROR-600"])
def test_provider_side_ecos_codes_are_retried(code):
    assert not central_bank_assets_weekly.is_unrecoverable_result(code)


def test_a_missing_api_key_fails_before_any_call(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    with pytest.raises(AirflowFailException, match="FRED_API_KEY"):
        central_bank_assets_weekly.require_env("FRED_API_KEY")
