"""DAG 안에 있는 순수 로직 검증.

수집기는 `tests/collectors/`가 덮지만 DAG 파일에도 조용히 틀리면 데이터가 비는 자리가
둘 있다. Yahoo 백필의 **보관 기간 가드**와 KIS 토큰 캐시다. 둘 다 실패해도 예외가 아니라
빈 결과나 발급 제한으로 나타나서 대시보드를 열기 전까지 모른다.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from airflow.exceptions import AirflowFailException
from pydantic import SecretStr

from dags import kis_quote_intraday, yahoo_quote_intraday
from dags.kis_quote_intraday import TOKEN_REFRESH_MARGIN, access_token
from dags.yahoo_quote_intraday import resolve_backfill_period
from modules.collectors.yahoo import BAR_RETENTION_DAYS


def period(start: str | None = None, end: str | None = None) -> dict[str, object]:
    return {"backfill_start": start, "backfill_end": end}


def test_no_backfill_params_means_ordinary_polling():
    assert resolve_backfill_period(period()) is None
    assert resolve_backfill_period({}) is None


def test_backfill_covers_the_whole_end_day():
    # 종료일은 포함이고 저장 경계는 열려 있다(`bar_at < until`). 그래서 상한이 다음 날 00:00이다.
    # 여기가 하루 어긋나면 마지막 날이 통째로 비고 아무 것도 실패하지 않는다.
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date()
    start, end = resolve_backfill_period(period(str(yesterday), str(yesterday)))

    assert start == datetime.combine(yesterday, datetime.min.time(), tzinfo=UTC)
    assert end == start + timedelta(days=1)


@pytest.mark.parametrize(
    "params",
    [period(start="2026-07-10"), period(end="2026-07-31")],
    ids=["start_only", "end_only"],
)
def test_backfill_needs_both_ends(params):
    with pytest.raises(AirflowFailException, match="together"):
        resolve_backfill_period(params)


def test_backfill_rejects_a_reversed_period():
    today = datetime.now(UTC).date()
    with pytest.raises(AirflowFailException, match="is after"):
        resolve_backfill_period(period(str(today), str(today - timedelta(days=3))))


def test_backfill_rejects_a_value_that_is_not_a_date():
    with pytest.raises(AirflowFailException, match="ISO date"):
        resolve_backfill_period(period("2026-07-32", "2026-07-31"))


def test_backfill_stops_before_yahoo_stops_keeping_bars():
    # Yahoo는 1분봉을 약 30일만 보관한다. 넘겨서 요청하면 오류가 아니라 빈 응답이 와서
    # 백필이 됐는지 안 됐는지 알 수 없다. 그래서 태스크가 시작하기 전에 막는다.
    too_old = (datetime.now(UTC) - timedelta(days=BAR_RETENTION_DAYS + 1)).date()
    with pytest.raises(AirflowFailException, match=str(BAR_RETENTION_DAYS)):
        resolve_backfill_period(period(str(too_old), str(datetime.now(UTC).date())))


class FakeVariable:
    """`Variable.get`/`set`만 흉내 낸다. 토큰 캐시가 쓰는 건 그 둘뿐이다."""

    def __init__(self, stored: str | None = None) -> None:
        self.stored = stored
        self.writes = 0

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.stored if self.stored is not None else default

    def set(self, key: str, value: str) -> None:
        self.stored = value
        self.writes += 1


def cached_token(token: str, expires_in: timedelta) -> str:
    return json.dumps({"token": token, "expires_at": (datetime.now(UTC) + expires_in).isoformat()})


@pytest.fixture
def issued(monkeypatch):
    """발급 호출을 센다. 실제 KIS 발급은 횟수 제한이 있어 여기서는 절대 부르지 않는다."""
    calls = []

    def fake_issue_token(app_key: SecretStr, app_secret: SecretStr):
        calls.append(app_key)
        return SecretStr("fresh"), datetime.now(UTC) + timedelta(hours=24)

    monkeypatch.setattr(kis_quote_intraday, "issue_token", fake_issue_token)
    return calls


def token_for(monkeypatch, variable: FakeVariable, *, force: bool = False) -> SecretStr:
    monkeypatch.setattr(kis_quote_intraday, "Variable", variable)
    return access_token(SecretStr("key"), SecretStr("secret"), force=force)


def test_a_live_cached_token_is_reused(monkeypatch, issued):
    variable = FakeVariable(cached_token("cached", timedelta(hours=12)))

    assert token_for(monkeypatch, variable).get_secret_value() == "cached"
    assert issued == []


def test_a_token_close_to_expiry_is_replaced_before_it_dies(monkeypatch, issued):
    # 만료 직전 토큰을 그대로 쓰면 폴링 도중 401이 난다. 여유분 안쪽이면 미리 갈아 끼운다.
    variable = FakeVariable(cached_token("stale", TOKEN_REFRESH_MARGIN - timedelta(minutes=1)))

    assert token_for(monkeypatch, variable).get_secret_value() == "fresh"
    assert len(issued) == 1
    assert json.loads(variable.stored)["token"] == "fresh"


@pytest.mark.parametrize(
    "stored",
    [None, "not json", json.dumps({"token": "x"}), json.dumps({"token": "x", "expires_at": "언젠가"})],
    ids=["empty", "broken_json", "missing_expiry", "unparsable_expiry"],
)
def test_an_unusable_cache_falls_back_to_issuing(monkeypatch, issued, stored):
    # 캐시가 깨졌다고 태스크가 죽으면 안 된다. 새로 받고 캐시를 덮어쓴다.
    variable = FakeVariable(stored)

    assert token_for(monkeypatch, variable).get_secret_value() == "fresh"
    assert len(issued) == 1


def test_force_ignores_a_live_cache(monkeypatch, issued):
    # 401을 만났을 때 쓰는 경로다. 캐시가 살아 있어도 다시 받아야 그 401을 벗어난다.
    variable = FakeVariable(cached_token("cached", timedelta(hours=12)))

    assert token_for(monkeypatch, variable, force=True).get_secret_value() == "fresh"
    assert len(issued) == 1


def test_the_dags_stay_on_their_intended_schedules():
    # 이 둘은 주석으로만 지켜지던 값이다. Yahoo 는 한국 장중의 미국 선물이 목적이라
    # 24시간이어야 하고, KIS 는 국내 정규장만 감싸면 된다.
    assert yahoo_quote_intraday.yahoo_quote_intraday.schedule == "*/5 * * * *"
    assert kis_quote_intraday.kis_quote_intraday.schedule == "*/5 8-16 * * 1-5"
