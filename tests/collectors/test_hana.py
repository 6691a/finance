import re
from datetime import UTC, date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Self

import pendulum
import pytest
from pydantic import ValidationError
from sqlalchemy import Table

from apps.models.finance import ExchangeRate
from modules.collectors.hana import (
    EARLIEST_QUOTATION_DATE,
    EXCHANGE_RATE_UPSERT,
    KST,
    MAX_DAY_OFFSET,
    UPSERT_PAGE_SIZE,
    HanaCurrency,
    HanaHTTPError,
    HanaPayloadError,
    HanaRate,
    HanaRateRequest,
    HanaResponse,
    fetch_rates,
    latest_quotation_date,
    parse_rates,
    quotation_date_for,
    store_rates,
)

QUOTATION_DATE = date(2026, 8, 5)
STARTED_AT = datetime(2026, 8, 5, 23, 0, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 5, 23, 0, 1, tzinfo=UTC)

# 실제 응답의 칸 순서. 회차, 시간, 현찰 2칸, 송금 2칸, 외화수표, 매매기준율, 직전대비, 환가료율, 미화환산율.
ROW_TEMPLATE = (
    "<tr>"
    "<td>{round}</td><td>{time}</td>"
    "<td>1,448.41</td><td>1,398.59</td>"
    "<td>1,437.40</td><td>1,409.60</td>"
    "<td>1,407.43</td><td>1,423.50</td>"
    "<td>0.10</td><td>5.50050</td><td>1.0000</td>"
    "</tr>"
)


def table_html(rows: str) -> bytes:
    return f'<table class="tblBasic"><tbody>{rows}</tbody></table>'.encode()


# 표는 회차 내림차순으로 온다. 1회차는 KST 08:25, 마지막 회차는 자정을 넘긴 다음 날 새벽이다.
PAYLOAD = table_html(
    ROW_TEMPLATE.format(round=3, time="00:10:00")
    + ROW_TEMPLATE.format(round=2, time="23:50:00")
    + ROW_TEMPLATE.format(round=1, time="08:25:25")
)


def request_for(currency: HanaCurrency = HanaCurrency.USD) -> HanaRateRequest:
    return HanaRateRequest(currency=currency, quotation_date=QUOTATION_DATE)


def response_for(body: bytes = PAYLOAD, currency: HanaCurrency = HanaCurrency.USD) -> HanaResponse:
    return HanaResponse(
        request=request_for(currency),
        body=body,
        status=200,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )


def rate_for(round_number: int = 1, quoted_at: datetime | None = None) -> HanaRate:
    return HanaRate(
        currency=HanaCurrency.USD,
        round=round_number,
        quoted_at=quoted_at or datetime(2026, 8, 5, 8, 25, 25, tzinfo=KST),
        buy=Decimal("1448.41"),
        sell=Decimal("1398.59"),
        send=Decimal("1437.40"),
        receive=Decimal("1409.60"),
        standard=Decimal("1423.50"),
    )


class FakeCursor:
    """PEP 249 커서. psycopg2가 없는 자리에서 `store_rates`가 타는 `executemany` 경로다.

    `calls`는 어느 경로를 탔든 (문장, 파라미터) 한 쌍씩 남겨서, 배치로 바꾸기 전 테스트가
    보던 것과 같은 모양을 유지한다.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.batches = 0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple) -> None:
        self.calls.append((statement, parameters))

    def executemany(self, statement: str, parameters) -> None:
        self.batches += 1
        self.calls.extend((statement, tuple(row)) for row in parameters)


class FakeConnection:
    def __init__(self) -> None:
        self.recorded_cursor = FakeCursor()

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


class FakeResponse:
    def __init__(self, body: bytes = PAYLOAD, status: int = 200) -> None:
        self.body = body
        self.status = status


@pytest.fixture(autouse=True)
def without_the_psycopg2_fast_path(monkeypatch):
    """저장 테스트를 PEP 249 경로에 고정한다.

    psycopg2가 설치돼 있으면 `store_rates`는 `execute_batch`를 탄다. 그건 문장을 묶어
    보내므로 커서에 도착하는 SQL이 드라이버 사정에 따라 달라진다. 파라미터 바인딩 같은
    이 모듈의 계약을 검증하려면 행 단위가 그대로 보이는 경로여야 한다.
    고속 경로 자체는 `test_store_uses_the_psycopg2_fast_path_when_the_driver_offers_it`이 본다.
    """
    monkeypatch.setattr("modules.collectors.hana._execute_batch", None)


def inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    # 쿼리 파일은 컬럼마다 `-- 설명`을 달아 둔다. 이름만 남기려면 먼저 주석을 걷어낸다.
    names = re.sub(r"--[^\n]*", "", columns.group(1))
    return tuple(name.strip() for name in names.split(",") if name.strip())


def placeholder_count(statement: str) -> int:
    values = re.search(r"VALUES \(([^)]+)\)", statement, re.DOTALL)
    assert values is not None
    return values.group(1).count("%s")


def required_columns(table: Table) -> set[str]:
    """DB가 채워 주지 않는 NOT NULL 컬럼. INSERT가 하나라도 빠뜨리면 런타임에 터진다."""
    return {
        column.name
        for column in table.columns
        if not column.nullable and column.server_default is None and not column.primary_key
    }


def test_upsert_matches_the_model_and_its_natural_key():
    # 수집기는 ORM 없이 문자열 SQL을 쓴다. 컬럼 이름이 어긋나면 실행 시점에야 드러나므로
    # 모델 metadata와 여기서 맞춰 둔다.
    table = ExchangeRate.__table__
    columns = inserted_columns(EXCHANGE_RATE_UPSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    assert placeholder_count(EXCHANGE_RATE_UPSERT) == len(columns)

    natural_key = next(
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.name == "unique_currency_date_time_round"
    )
    assert f"ON CONFLICT ({', '.join(natural_key)}) DO UPDATE" in EXCHANGE_RATE_UPSERT


def test_request_sends_the_kst_quotation_date_and_the_round_publication_code():
    assert request_for().form_data == {"pbldDvCd": "0", "curCd": "USD", "inqDt": "20260805"}


def test_request_rejects_a_currency_hana_does_not_publish():
    with pytest.raises(ValidationError):
        HanaRateRequest(currency="KRW", quotation_date=QUOTATION_DATE)


def test_quotation_date_is_the_kst_day_before_the_interval_end():
    # 스케줄은 KST 08:00이다. 그 시점에 완결돼 있는 건 전날 고시다.
    assert quotation_date_for(datetime(2026, 8, 5, 23, 0, tzinfo=UTC)) == date(2026, 8, 5)


def test_quotation_date_survives_the_pendulum_datetime_airflow_passes():
    # Airflow는 pendulum DateTime을 넘긴다. pendulum은 `datetime - timedelta` 결과를 UTC로
    # 정규화해서 시간대 라벨을 지운다. 그래서 KST 날짜를 먼저 뽑고 `date`에서 하루를 뺀다.
    # 표준 라이브러리 datetime만으로 검증하면 이 차이가 드러나지 않는다.
    interval_end = pendulum.datetime(2026, 6, 30, 23, 0, tz="UTC")  # KST 2026-07-01 08:00

    assert quotation_date_for(interval_end) == date(2026, 6, 30)
    assert quotation_date_for(interval_end.astimezone(UTC)) == quotation_date_for(interval_end)


def test_quotation_date_uses_the_kst_day_not_the_utc_day():
    # UTC 2026-08-05 23:00은 KST 2026-08-06 08:00이다. UTC 날짜로 계산하면 하루 어긋난다.
    interval_end = datetime(2026, 8, 5, 23, 0, tzinfo=UTC)

    assert interval_end.date() == date(2026, 8, 5)
    assert interval_end.astimezone(KST).date() == date(2026, 8, 6)
    assert quotation_date_for(interval_end) == date(2026, 8, 5)


def test_request_rejects_a_quotation_date_before_the_supported_range():
    # 고시일자 하나가 통화당 1500행 가까이 된다. `catchup`이나 손으로 만든 백필이 여기서 막힌다.
    with pytest.raises(ValidationError, match="must not be earlier than"):
        HanaRateRequest(currency=HanaCurrency.USD, quotation_date=EARLIEST_QUOTATION_DATE - timedelta(days=1))


def test_request_accepts_the_first_day_of_the_supported_range():
    request = HanaRateRequest(currency=HanaCurrency.USD, quotation_date=EARLIEST_QUOTATION_DATE)

    assert request.quotation_date == date(2026, 7, 1)


def test_request_bounds_the_range_at_the_latest_quotation_date(monkeypatch):
    # 경계를 고정한 날짜로 확인한다. 벽시계를 그대로 쓰면 KST 자정을 걸칠 때 두 번의
    # `now()`가 다른 날을 가리켜 테스트가 흔들린다.
    today = date(2026, 8, 5)
    monkeypatch.setattr("modules.collectors.hana.latest_quotation_date", lambda: today)

    assert HanaRateRequest(currency=HanaCurrency.USD, quotation_date=today).quotation_date == today
    with pytest.raises(ValidationError, match="must not be later than"):
        HanaRateRequest(currency=HanaCurrency.USD, quotation_date=today + timedelta(days=1))


def test_latest_quotation_date_follows_kst_not_utc():
    # KST 00:00~09:00 사이에는 UTC 날짜가 하루 뒤처진다. UTC 오늘로 막으면 그 시간대에
    # 정상 고시일자를 거절한다.
    assert latest_quotation_date() == datetime.now(KST).date()


def test_request_and_response_are_frozen_so_a_retry_cannot_mutate_them():
    response = response_for()

    with pytest.raises(ValidationError):
        response.request.currency = HanaCurrency.JPY
    with pytest.raises(ValidationError):
        response.status = 500


def test_response_normalizes_timestamps_to_utc_and_rejects_naive_ones():
    response = HanaResponse(
        request=request_for(),
        body=PAYLOAD,
        status=200,
        started_at=datetime(2026, 8, 6, 8, 0, tzinfo=timezone(timedelta(hours=9))),
        completed_at=COMPLETED_AT,
    )

    assert response.started_at == STARTED_AT
    assert response.started_at.utcoffset() == timedelta(0)

    with pytest.raises(ValidationError):
        HanaResponse(
            request=request_for(),
            body=PAYLOAD,
            status=200,
            started_at=datetime(2026, 8, 5, 23, 0),  # noqa: DTZ001
            completed_at=COMPLETED_AT,
        )


def test_parse_reads_the_standard_rate_from_its_own_column():
    # 옛 구현은 7번째 칸(`외화수표 파실 때`, 1,407.43)을 매매 기준율로 저장했다.
    rates = parse_rates(response_for())

    assert rates[0].standard == Decimal("1423.50")
    assert (rates[0].buy, rates[0].sell) == (Decimal("1448.41"), Decimal("1398.59"))
    assert (rates[0].send, rates[0].receive) == (Decimal("1437.40"), Decimal("1409.60"))


def test_parse_returns_rounds_in_ascending_order():
    assert [rate.round for rate in parse_rates(response_for())] == [1, 2, 3]


def test_parse_accepts_a_friday_table_that_wraps_past_midnight_twice():
    # 금요일 고시는 다음 영업일 개장까지 이어져 토·일요일 자정을 함께 넘는다.
    body = table_html(
        ROW_TEMPLATE.format(round=1, time="08:25:25")
        + ROW_TEMPLATE.format(round=2, time="23:50:00")
        + ROW_TEMPLATE.format(round=3, time="12:24:47")
        + ROW_TEMPLATE.format(round=4, time="06:57:02")
    )

    days = [rate.quoted_at.astimezone(KST).date() for rate in parse_rates(response_for(body=body))]

    assert days == [
        QUOTATION_DATE,
        QUOTATION_DATE,
        QUOTATION_DATE + timedelta(days=1),
        QUOTATION_DATE + timedelta(days=2),
    ]


def test_parse_rejects_a_table_that_wraps_past_midnight_beyond_the_limit():
    # 상한을 넘게 되감겼으면 회차와 시각이 어긋난 것이고, 그냥 두면 날짜가 조용히 밀린 채 저장된다.
    rows = "".join(
        ROW_TEMPLATE.format(round=number, time="23:50:00" if number % 2 else "00:10:00")
        for number in range(1, MAX_DAY_OFFSET * 2 + 4)
    )

    with pytest.raises(HanaPayloadError, match="wrapped past midnight more than"):
        parse_rates(response_for(body=table_html(rows)))


def test_parse_moves_rounds_past_midnight_to_the_next_kst_day():
    rates = {rate.round: rate for rate in parse_rates(response_for())}

    assert rates[1].quoted_at.astimezone(KST) == datetime(2026, 8, 5, 8, 25, 25, tzinfo=KST)
    assert rates[2].quoted_at.astimezone(KST) == datetime(2026, 8, 5, 23, 50, tzinfo=KST)
    # 3회차는 시각이 되감겼으므로 고시일자 다음 날이다.
    assert rates[3].quoted_at.astimezone(KST) == datetime(2026, 8, 6, 0, 10, tzinfo=KST)


def test_parse_stores_utc_date_and_time_for_the_existing_table_layout():
    rates = {rate.round: rate for rate in parse_rates(response_for())}

    # KST 08:25:25 = UTC 전날 23:25:25.
    assert rates[1].observation_date == date(2026, 8, 4)
    assert rates[1].observation_time == time(23, 25, 25)
    # KST 다음 날 00:10 = UTC 같은 날 15:10.
    assert rates[3].observation_date == date(2026, 8, 5)
    assert rates[3].observation_time == time(15, 10)


def test_parse_returns_nothing_for_a_holiday_with_an_empty_table():
    assert parse_rates(response_for(body=table_html(""))) == ()


def test_parse_rejects_a_response_without_the_rate_table():
    with pytest.raises(HanaPayloadError):
        parse_rates(response_for(body=b"<html><body>maintenance</body></html>"))


def test_parse_rejects_a_row_whose_column_count_changed():
    body = table_html("<tr><td>1</td><td>08:25:25</td><td>1,448.41</td></tr>")

    with pytest.raises(HanaPayloadError):
        parse_rates(response_for(body=body))


@pytest.mark.parametrize("cell", ["", "-", "NaN", "Infinity"])
def test_parse_rejects_a_broken_rate_cell_instead_of_storing_a_partial_result(cell):
    body = table_html(
        "<tr><td>1</td><td>08:25:25</td>"
        f"<td>{cell}</td><td>1,398.59</td><td>1,437.40</td><td>1,409.60</td>"
        "<td>1,407.43</td><td>1,423.50</td><td>0.10</td><td>5.50050</td><td>1.0000</td></tr>"
    )

    with pytest.raises(HanaPayloadError):
        parse_rates(response_for(body=body))


def test_parse_rejects_a_broken_time_cell():
    body = table_html(ROW_TEMPLATE.format(round=1, time="nope"))

    with pytest.raises(HanaPayloadError):
        parse_rates(response_for(body=body))


def test_rate_is_frozen_and_rejects_a_naive_timestamp():
    rate = rate_for()

    with pytest.raises(ValidationError):
        rate.standard = Decimal(1)
    with pytest.raises(ValidationError):
        HanaRate(
            currency=HanaCurrency.USD,
            round=1,
            quoted_at=datetime(2026, 8, 5, 8, 25, 25),  # noqa: DTZ001
            buy=Decimal(1),
            sell=Decimal(1),
            send=Decimal(1),
            receive=Decimal(1),
            standard=Decimal(1),
        )


def test_fetch_raises_on_a_non_success_status(monkeypatch):
    monkeypatch.setattr(
        "modules.collectors.hana.Fetcher.post",
        lambda *args, **kwargs: FakeResponse(status=503),
    )

    with pytest.raises(HanaHTTPError) as raised:
        fetch_rates(request_for())

    assert raised.value.status == 503


def test_fetch_records_an_aware_utc_span(monkeypatch):
    monkeypatch.setattr("modules.collectors.hana.Fetcher.post", lambda *args, **kwargs: FakeResponse())

    response = fetch_rates(request_for())

    assert response.status == 200
    assert response.body == PAYLOAD
    assert response.started_at.utcoffset() == timedelta(0)
    assert response.started_at <= response.completed_at


def test_store_writes_one_upsert_per_round():
    connection = FakeConnection()

    assert store_rates(connection, parse_rates(response_for())) == 3

    statements = [statement for statement, _ in connection.recorded_cursor.calls]
    assert len(statements) == 3
    assert all("INSERT INTO exchange_rate" in statement for statement in statements)
    assert all("ON CONFLICT (currency, date, time, round) DO UPDATE" in statement for statement in statements)


def test_store_sends_every_round_in_one_call_not_one_round_per_round_trip():
    # 고시일자 하나가 통화당 1500행 가까이 된다. 행마다 왕복하면 원격 DB에서 지연이 그만큼
    # 곱해진다. psycopg2가 없는 환경에서도 최소한 `executemany` 한 번으로 끝나야 한다.
    connection = FakeConnection()

    store_rates(connection, parse_rates(response_for()))

    assert connection.recorded_cursor.batches == 1
    assert len(connection.recorded_cursor.calls) == 3


def test_store_uses_the_psycopg2_fast_path_when_the_driver_offers_it(monkeypatch):
    # psycopg2의 `executemany`는 행마다 왕복해서 반복문과 같다. `execute_batch`만이 실제로
    # 문장을 묶어 보낸다. 드라이버가 그걸 주면 반드시 그 경로를 타야 한다.
    sent = []

    def fake_execute_batch(cursor, statement, parameters, page_size):
        sent.append((statement, list(parameters), page_size))

    monkeypatch.setattr("modules.collectors.hana._execute_batch", fake_execute_batch)
    connection = FakeConnection()

    assert store_rates(connection, parse_rates(response_for())) == 3

    assert connection.recorded_cursor.calls == []
    statement, parameters, page_size = sent[0]
    assert "ON CONFLICT (currency, date, time, round) DO UPDATE" in statement
    assert len(parameters) == 3
    assert page_size == UPSERT_PAGE_SIZE


def test_store_binds_every_value_as_a_parameter_instead_of_inlining_it():
    connection = FakeConnection()

    store_rates(connection, [rate_for()])

    currency, round_number, observation_date, observation_time, buy, sell, send, receive, standard = (
        connection.recorded_cursor.calls[0][1]
    )

    assert (currency, round_number) == ("USD", 1)
    assert (observation_date, observation_time) == (date(2026, 8, 4), time(23, 25, 25))
    assert (buy, sell, send, receive, standard) == (
        Decimal("1448.41"),
        Decimal("1398.59"),
        Decimal("1437.40"),
        Decimal("1409.60"),
        Decimal("1423.50"),
    )


def test_store_writes_nothing_for_an_empty_result():
    connection = FakeConnection()

    assert store_rates(connection, []) == 0
    assert connection.recorded_cursor.calls == []


def test_store_repeats_the_same_upsert_for_a_rerun_of_the_same_day():
    first, second = FakeConnection(), FakeConnection()
    rates = parse_rates(response_for())

    assert store_rates(first, rates) == store_rates(second, rates) == 3
    assert first.recorded_cursor.calls == second.recorded_cursor.calls
