"""DAG 파일이 공유하는 도우미. 예외 종류와 메시지가 DAG마다 복사돼 있던 것을 한 벌로 잰다."""

from datetime import date, timedelta
from typing import ClassVar, Self

import pytest
from airflow.sdk.exceptions import AirflowFailException, AirflowSkipException
from pydantic import SecretStr

from modules import dag_common
from modules.collectors.kis import KisHTTPError
from modules.period import LOOKBACK_DAYS, SPAN_CALENDAR_DAYS


class FakeCursor:
    def __init__(self, row: tuple | None) -> None:
        self.row = row

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple) -> None:
        self.parameters = parameters

    def fetchone(self) -> tuple | None:
        return self.row


class FakeConnection:
    def __init__(self, row: tuple | None) -> None:
        self.recorded_cursor = FakeCursor(row)

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


# ---------------------------------------------------------------------------
# 환경
# ---------------------------------------------------------------------------


def test_missing_kis_credentials_fail_without_retry(monkeypatch):
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.setenv("KIS_APP_SECRET", "secret")

    with pytest.raises(AirflowFailException, match="KIS_APP_KEY and KIS_APP_SECRET are required"):
        dag_common.kis_credentials()


def test_kis_credentials_are_wrapped_so_logs_cannot_print_them(monkeypatch):
    monkeypatch.setenv("KIS_APP_KEY", "app-key")
    monkeypatch.setenv("KIS_APP_SECRET", "app-secret")

    app_key, app_secret = dag_common.kis_credentials()

    assert isinstance(app_key, SecretStr) and isinstance(app_secret, SecretStr)
    assert "app-key" not in str(app_key) and "app-secret" not in str(app_secret)
    assert app_secret.get_secret_value() == "app-secret"


def test_slack_settings_name_the_missing_channel_variable(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret")
    monkeypatch.delenv("SLACK_CHANNEL_OPS", raising=False)

    with pytest.raises(AirflowFailException, match="SLACK_BOT_TOKEN and SLACK_CHANNEL_OPS are required"):
        dag_common.slack_settings("SLACK_CHANNEL_OPS")


def test_slack_settings_default_to_the_market_channel(monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secret")
    monkeypatch.setenv("SLACK_CHANNEL_MARKET", "C-market")

    token, channel = dag_common.slack_settings()

    assert channel == "C-market"
    assert "xoxb-secret" not in str(token)


def test_a_missing_env_fails_before_any_call(monkeypatch):
    monkeypatch.delenv("FRED_API_KEY", raising=False)

    with pytest.raises(AirflowFailException, match="FRED_API_KEY is required"):
        dag_common.require_env("FRED_API_KEY")


# ---------------------------------------------------------------------------
# 조회 구간
# ---------------------------------------------------------------------------


def test_observation_period_params_carry_titles_and_descriptions():
    params = dag_common.observation_period_params(lookback_hint="구간을 지정하지 않을 때만 쓴다.")

    assert list(params) == ["observation_start", "observation_end", "lookback_days"]
    for param in params.values():
        assert param.schema.get("title")
        assert param.description
    assert params["observation_start"].schema["title"] == "조회 시작 관측일"
    assert params["observation_end"].schema["title"] == "조회 종료 관측일"
    assert params["lookback_days"].value == LOOKBACK_DAYS
    assert params["lookback_days"].description == "구간을 지정하지 않을 때만 쓴다."


def test_observation_period_params_take_the_dag_specific_wording():
    params = dag_common.observation_period_params(
        lookback_default=800,
        lookback_hint="발표가 밀린 계열에 맞춘 값이다.",
        start_title="조회 시작 거래일",
        end_title="조회 종료 거래일",
        start_hint="그 날이 속한 달부터 받는다.",
        end_hint="그 날이 속한 달까지 받는다.",
    )

    assert params["observation_start"].schema["title"] == "조회 시작 거래일"
    assert params["observation_start"].description == "그 날이 속한 달부터 받는다."
    assert params["observation_end"].schema["title"] == "조회 종료 거래일"
    assert params["observation_end"].description == "그 날이 속한 달까지 받는다."
    assert params["lookback_days"].value == 800
    assert params["lookback_days"].schema["minimum"] == 1


def test_resolve_period_or_fail_uses_the_given_lookback():
    context = {"params": {"observation_end": "2026-08-31"}}

    assert dag_common.resolve_period_or_fail(context, 45) == (date(2026, 7, 18), date(2026, 8, 31))


def test_a_bad_period_parameter_fails_without_retry():
    """파라미터를 고치기 전에는 재시도해도 같다. `PeriodError`의 문장이 그대로 실린다."""
    context = {"params": {"observation_start": "2026-09-02", "observation_end": "2026-09-01"}}

    with pytest.raises(AirflowFailException, match=r"observation_start \(2026-09-02\) is after observation_end"):
        dag_common.resolve_period_or_fail(context)


@pytest.mark.parametrize("given", ["20260821", "2026-W34", "yesterday"])
def test_an_unreadable_calendar_day_fails_without_retry(given):
    with pytest.raises(AirflowFailException, match="end_date must be YYYY-MM-DD"):
        dag_common.calendar_day_or_fail(given, "end_date")


def test_an_empty_start_date_keeps_the_fixed_span():
    end_date = date(2026, 8, 25)

    assert dag_common.requested_start_date(end_date, {}) == end_date - timedelta(days=SPAN_CALENDAR_DAYS)


def test_a_start_date_after_the_end_fails_before_any_call():
    """조용히 빈 구간이 되면 0건 저장을 정상으로 읽는다."""
    with pytest.raises(AirflowFailException, match="start_date 2026-08-26 must not be after end_date 2026-08-25"):
        dag_common.requested_start_date(date(2026, 8, 25), {"start_date": "2026-08-26"})


def test_an_iso_week_start_date_is_rejected():
    """`date.fromisoformat`은 `2026-W34`도 받아 그 주의 월요일로 바꾼다."""
    with pytest.raises(AirflowFailException, match="must be YYYY-MM-DD"):
        dag_common.requested_start_date(date(2026, 8, 25), {"start_date": "2026-W34"})


# ---------------------------------------------------------------------------
# 휴장일과 실패 분류
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "skips"),
    [
        ((False,), True),
        ((True,), False),
        # 아직 판정하지 않았거나 캘린더가 그 날짜를 못 채웠다. 모르면 계속한다.
        ((None,), False),
        (None, False),
    ],
)
def test_krx_skip_only_on_a_confirmed_holiday(row, skips):
    connection = FakeConnection(row)
    day = date(2026, 8, 17)

    if skips:
        with pytest.raises(AirflowSkipException, match="KRX is closed on 2026-08-17"):
            dag_common.skip_unless_krx_open(connection, day)
    else:
        dag_common.skip_unless_krx_open(connection, day)

    assert connection.recorded_cursor.parameters == ("KRX", day)


def test_us_skip_asks_the_us_calendar():
    connection = FakeConnection((False,))

    with pytest.raises(AirflowSkipException, match="US equity market was closed on 2026-08-24"):
        dag_common.skip_unless_us_open(connection, date(2026, 8, 24))

    assert connection.recorded_cursor.parameters == ("US_EQUITY", date(2026, 8, 24))


@pytest.mark.parametrize("code", ["INFO-100", "ERROR-100", "ERROR-300"])
def test_unrecoverable_ecos_codes_are_not_retried(code):
    assert dag_common.is_unrecoverable_result(code)


@pytest.mark.parametrize("code", ["INFO-200", "ERROR-500", "ERROR-600"])
def test_provider_side_ecos_codes_are_retried(code):
    assert not dag_common.is_unrecoverable_result(code)


# ---------------------------------------------------------------------------
# KIS 호출
# ---------------------------------------------------------------------------


class FakeCollector:
    """모든 KIS 수집기와 같은 생성자 모양. `statuses`가 호출마다 낼 HTTP 상태다."""

    statuses: ClassVar[list[int]] = []

    def __init__(self, token: SecretStr, app_key: SecretStr, app_secret: SecretStr) -> None:
        self.token = token

    def fetch(self, symbol: str) -> str:
        if FakeCollector.statuses:
            raise KisHTTPError(FakeCollector.statuses.pop(0))
        return f"{symbol}@{self.token.get_secret_value()}"


@pytest.fixture
def tokens(monkeypatch):
    """`Variable` 캐시 대신 발급 호출을 기록한다. `force=True`는 재발급이다."""
    issued: list[bool] = []

    def fake_access_token(store, app_key, app_secret, force=False):
        issued.append(force)
        return SecretStr("reissued" if force else "cached")

    monkeypatch.setattr(dag_common, "access_token", fake_access_token)
    FakeCollector.statuses = []
    return issued


def _call(statuses: list[int], label: str = "") -> str:
    FakeCollector.statuses = statuses
    collector = FakeCollector(SecretStr("cached"), SecretStr("k"), SecretStr("s"))
    return dag_common.call_with_token_reissue(
        collector, FakeCollector.fetch, "KOSPI", app_key=SecretStr("k"), app_secret=SecretStr("s"), label=label
    )


def test_a_401_reissues_the_token_once_and_retries(tokens):
    assert _call([401]) == "KOSPI@reissued"
    assert tokens == [True]


def test_a_second_401_is_not_absorbed(tokens):
    """한 번만 재발급한다. 두 번째 401은 그대로 올라가 DAG가 판단한다."""
    with pytest.raises(KisHTTPError) as caught:
        _call([401, 401])

    assert caught.value.status == 401
    assert tokens == [True]


def test_an_unrecoverable_status_fails_with_the_label(tokens):
    with pytest.raises(AirflowFailException, match=r"^KOSPI: KIS request failed with HTTP 403$"):
        _call([403], label="KOSPI")

    assert tokens == []


def test_an_unrecoverable_status_without_a_label_keeps_the_bare_message(tokens):
    with pytest.raises(AirflowFailException, match=r"^KIS request failed with HTTP 404$"):
        _call([404])


def test_other_http_errors_are_raised_for_the_dag_to_classify(tokens):
    with pytest.raises(KisHTTPError) as caught:
        _call([500])

    assert caught.value.status == 500
    assert tokens == []
