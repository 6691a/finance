"""DAG 객체와 실패 판정만 검증한다.

수집·검증 규칙은 `modules/collectors/indicator/`의 `ecos.py`·`fred.py`·`boe.py`에 있고
`tests/collectors/`가 덮는다. 설계는 docs/collection/policy-rate-collection.md다.
"""

from datetime import UTC, date, datetime

import pytest
from airflow.sdk.exceptions import AirflowFailException

from dags import policy_rate_weekly
from modules.collectors.indicator.boe import POLICY_DATASET
from modules.collectors.indicator.boe import POLICY_RATE_SERIES as BOE_POLICY_SERIES
from modules.collectors.indicator.ecos import POLICY_RATE_SERIES as ECOS_POLICY_SERIES
from modules.collectors.indicator.fred import POLICY_RATE_SERIES as FRED_POLICY_SERIES

DAG = policy_rate_weekly.policy_rate_weekly


def test_the_dag_runs_once_a_week_after_the_weekend():
    # KST 월 09:00 = UTC 일 00:00. 정책금리는 통화정책 회의 때만 바뀌므로 일별로 돌 값어치가 없다.
    assert DAG.schedule == "0 9 * * 1"
    assert DAG.max_active_runs == 1


def test_one_task_per_provider_so_one_failure_does_not_take_the_others():
    # 제공처가 셋이라 태스크도 셋이다. 하나가 실패해도 나머지는 저장되고 재시도도 그것만 돈다.
    assert set(DAG.task_dict) == {"collect_ecos", "collect_fred", "collect_boe"}

    # 태스크 사이에 의존이 없어야 앞의 실패가 뒤를 skip으로 만들지 않는다.
    for task in DAG.task_dict.values():
        assert not task.upstream_task_ids


def test_the_window_is_wide_enough_to_survive_a_missed_run():
    # 주 1회라 창이 좁으면 실행이 한 번 밀린 주가 그대로 빈다. 월별 일본이 달 경계를 반드시 문다.
    assert policy_rate_weekly.LOOKBACK_DAYS_POLICY == 45
    assert DAG.params["lookback_days"] == 45


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


def test_the_five_central_banks_are_split_across_the_three_providers():
    collected = set(ECOS_POLICY_SERIES) | set(FRED_POLICY_SERIES) | set(BOE_POLICY_SERIES)

    assert collected == {"KRBASE", "JPBASE_M", "DFEDTARU", "EADFR", "GBBASE"}
    # 같은 IADB를 국채 DAG도 부른다. source_key가 갈려야 어느 묶음을 받았는지 되짚는다.
    assert POLICY_DATASET.source_key == "bank_rate"


def test_one_failed_series_kills_the_task():
    """주 1회라 다음 실행이 곧 같은 창을 다시 보지 않는다(한 주 뒤다).

    사유에 쉼표가 들어가므로 구분자는 `;`다.
    """
    with pytest.raises(AirflowFailException, match="KRBASE.*; .*JPBASE_M"):
        policy_rate_weekly.require_no_failures("ECOS", ["KRBASE(boom, again)", "JPBASE_M(boom)"])


def test_no_failure_lets_the_task_succeed():
    assert policy_rate_weekly.require_no_failures("FRED", []) is None


@pytest.mark.parametrize("code", ["INFO-100", "ERROR-100", "ERROR-300"])
def test_unrecoverable_ecos_codes_are_not_retried(code):
    assert policy_rate_weekly.is_unrecoverable_result(code)


@pytest.mark.parametrize("code", ["INFO-200", "ERROR-500", "ERROR-600"])
def test_provider_side_ecos_codes_are_retried(code):
    assert not policy_rate_weekly.is_unrecoverable_result(code)


def test_a_missing_api_key_fails_before_any_call(monkeypatch):
    monkeypatch.delenv("ECOS_API_KEY", raising=False)

    with pytest.raises(AirflowFailException, match="ECOS_API_KEY"):
        policy_rate_weekly.require_env("ECOS_API_KEY")


def test_zero_observations_in_the_window_fail_the_task():
    """45일 창에 0건은 발표 전이 아니라 식별자·제공처 고장이다(G-41). ECOS는 그 상태를
    `INFO-200`으로 답해 예외를 안 내니 여기서 세지 않으면 매주 "성공, 0건"이다."""
    with pytest.raises(AirflowFailException, match="ECOS.*2026-07-18..2026-08-31"):
        policy_rate_weekly.require_observations("ECOS", 0, date(2026, 7, 18), date(2026, 8, 31))


def test_any_observation_lets_the_task_succeed():
    assert policy_rate_weekly.require_observations("ECOS", 1, date(2026, 7, 18), date(2026, 8, 31)) is None
