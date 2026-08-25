"""apps.realtime WebSocket 수집기의 계약 테스트.

프레임 픽스처는 문서 3.5의 계약(파이프 4구획, 레코드당 46필드 캐럿 구분)으로 합성했다.
실 캡처 픽스처가 오면 필드 순서·PINGPONG 회신 방식·ACK 코드를 여기서 대조해 고정한다.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import SecretStr

from apps.models.market import StockExchange
from apps.models.raw import SourceStatus
from apps.realtime import heartbeat as heartbeat_module
from apps.realtime import repository as repository_module
from apps.realtime import service
from apps.realtime.aggregator import SESSION_WINDOWS, MinuteAggregator, in_session
from apps.realtime.frames import (
    FRAME_SPECS,
    KRX_FIELDS,
    KRX_TR_ID,
    KST,
    NXT_FIELDS,
    NXT_TR_ID,
    EncryptedFrameError,
    FrameContractError,
    PingPong,
    SubscribeResult,
    Tick,
    parse_control_frame,
    parse_data_frame,
)
from apps.realtime.service import (
    AuthRejectedError,
    DomesticStock,
    RealtimeSettings,
    build_registry,
    in_connect_window,
)

SUBSCRIBED = frozenset({"005930", "000660"})


def make_settings(**overrides) -> RealtimeSettings:
    values = {"app_key": SecretStr("key"), "app_secret": SecretStr("secret")}
    values.update(overrides)
    return RealtimeSettings(**values)


def data_frame(tr_id: str, records: list[dict[str, str]], encrypted: str = "0") -> str:
    spec = FRAME_SPECS[tr_id]
    fields: list[str] = []
    for record in records:
        values = ["0"] * spec.field_count
        for name, value in record.items():
            values[spec.index(name)] = value
        fields.extend(values)
    return f"{encrypted}|{tr_id}|{len(records)}|" + "^".join(fields)


def trade(code: str = "005930", day: str = "20260818", hour: str = "090102", price: str = "153000", volume: str = "120"):
    return {
        "MKSC_SHRN_ISCD": code,
        "BSOP_DATE": day,
        "STCK_CNTG_HOUR": hour,
        "STCK_PRPR": price,
        "CNTG_VOL": volume,
    }


def tick(
    exchange: StockExchange = StockExchange.KRX,
    code: str = "005930",
    hour: int = 9,
    minute: int = 1,
    second: int = 2,
    price: str = "153000",
    volume: int = 10,
) -> Tick:
    return Tick(
        exchange=exchange,
        stock_code=code,
        occurred_at=datetime(2026, 8, 18, hour, minute, second, tzinfo=KST),
        price=Decimal(price),
        volume=volume,
    )


# ---------------------------------------------------------------- 프레임 파싱


def test_krx_data_frame_parses_the_five_fields():
    ticks = parse_data_frame(data_frame(KRX_TR_ID, [trade()]), SUBSCRIBED)

    assert len(ticks) == 1
    parsed = ticks[0]
    assert parsed.exchange is StockExchange.KRX
    assert parsed.stock_code == "005930"
    # 09:01:02 KST = 00:01:02 UTC
    assert parsed.occurred_at == datetime(2026, 8, 18, 0, 1, 2, tzinfo=UTC)
    assert parsed.price == Decimal(153000)
    assert parsed.volume == 120


def test_nxt_field_list_differs_only_at_the_ccld_slot():
    difference = {index for index, (krx, nxt) in enumerate(zip(KRX_FIELDS, NXT_FIELDS, strict=True)) if krx != nxt}
    assert len(difference) == 1
    slot = difference.pop()
    assert KRX_FIELDS[slot] == "CCLD_DVSN"
    assert NXT_FIELDS[slot] == "CNTG_CLS_CODE"

    ticks = parse_data_frame(data_frame(NXT_TR_ID, [trade(code="000660", hour="080015")]), SUBSCRIBED)
    assert ticks[0].exchange is StockExchange.NXT


def test_multi_record_frame_yields_one_tick_per_record():
    frame = data_frame(KRX_TR_ID, [trade(hour="090102"), trade(hour="090103", price="153100")])

    ticks = parse_data_frame(frame, SUBSCRIBED)

    assert [t.price for t in ticks] == [Decimal(153000), Decimal(153100)]


def test_record_count_mismatch_is_a_contract_error():
    frame = data_frame(KRX_TR_ID, [trade()]).replace(f"|{KRX_TR_ID}|1|", f"|{KRX_TR_ID}|2|")

    with pytest.raises(FrameContractError):
        parse_data_frame(frame, SUBSCRIBED)


def test_wrong_field_count_is_a_contract_error():
    frame = data_frame(KRX_TR_ID, [trade()])
    truncated = frame.rsplit("^", 1)[0]

    with pytest.raises(FrameContractError):
        parse_data_frame(truncated, SUBSCRIBED)


def test_unknown_tr_id_is_rejected():
    frame = data_frame(KRX_TR_ID, [trade()]).replace(KRX_TR_ID, "H0UNKNOWN")

    with pytest.raises(FrameContractError):
        parse_data_frame(frame, SUBSCRIBED)


def test_unsubscribed_stock_code_is_rejected():
    # 구독하지 않은 종목이 섞여 오면 스트림 계약이 깨진 것이다. 조용히 저장하지 않는다.
    with pytest.raises(FrameContractError):
        parse_data_frame(data_frame(KRX_TR_ID, [trade(code="035420")]), SUBSCRIBED)


def test_encrypted_frame_is_quarantined_not_parsed():
    with pytest.raises(EncryptedFrameError):
        parse_data_frame(data_frame(KRX_TR_ID, [trade()], encrypted="1"), SUBSCRIBED)


def test_pingpong_frame_is_classified():
    raw = json.dumps({"header": {"tr_id": "PINGPONG", "datetime": "20260818100000"}})

    control = parse_control_frame(raw)

    assert isinstance(control, PingPong)
    assert control.raw == raw


def test_subscribe_ack_and_nack_are_distinguished():
    ack_frame = parse_control_frame(
        json.dumps(
            {
                "header": {"tr_id": KRX_TR_ID, "tr_key": "005930"},
                "body": {"rt_cd": "0", "msg_cd": "OPSP0000", "msg1": "SUBSCRIBE SUCCESS"},
            }
        )
    )
    nack_frame = parse_control_frame(
        json.dumps(
            {
                "header": {"tr_id": NXT_TR_ID, "tr_key": "000660"},
                "body": {"rt_cd": "1", "msg_cd": "OPSP0011", "msg1": "INVALID"},
            }
        )
    )

    assert isinstance(ack_frame, SubscribeResult) and ack_frame.ok
    assert isinstance(nack_frame, SubscribeResult) and not nack_frame.ok
    assert nack_frame.code == "OPSP0011"


# ---------------------------------------------------------------- 분봉 집계


def boundary(hour: int, minute: int) -> datetime:
    return datetime(2026, 8, 18, hour, minute, tzinfo=KST)


def test_minute_ohlcv_aggregation():
    aggregator = MinuteAggregator()
    aggregator.add(tick(second=2, price="153000", volume=10))
    aggregator.add(tick(second=30, price="153500", volume=5))
    aggregator.add(tick(second=59, price="152900", volume=7))

    bars = aggregator.flush_before(boundary(9, 2))

    assert len(bars) == 1
    bar = bars[0]
    assert bar.bar_at == datetime(2026, 8, 18, 9, 1, tzinfo=KST).astimezone(UTC)
    assert (bar.open, bar.high, bar.low, bar.close) == (
        Decimal(153000),
        Decimal(153500),
        Decimal(152900),
        Decimal(152900),
    )
    assert bar.volume == 22


def test_same_timestamp_ticks_use_arrival_order_for_open_close():
    aggregator = MinuteAggregator()
    aggregator.add(tick(second=10, price="100"))
    aggregator.add(tick(second=10, price="200"))

    bar = aggregator.flush_before(boundary(9, 2))[0]

    assert bar.open == Decimal(100)
    assert bar.close == Decimal(200)


def test_flush_only_closes_minutes_before_the_boundary():
    aggregator = MinuteAggregator()
    aggregator.add(tick(minute=1))
    aggregator.add(tick(minute=2))

    bars = aggregator.flush_before(boundary(9, 2))

    assert [bar.bar_at.astimezone(KST).minute for bar in bars] == [1]
    # 아직 열려 있는 9:02 분은 다음 flush가 닫는다.
    assert [bar.bar_at.astimezone(KST).minute for bar in aggregator.flush_before(boundary(9, 3))] == [2]


def test_tradeless_minutes_produce_no_rows():
    assert MinuteAggregator().flush_before(boundary(9, 2)) == ()


def test_late_tick_after_flush_is_dropped_and_counted():
    aggregator = MinuteAggregator()
    aggregator.add(tick(minute=1))
    aggregator.flush_before(boundary(9, 2))

    aggregator.add(tick(minute=1, second=59))

    assert aggregator.late_tick_count == 1
    assert aggregator.flush_before(boundary(9, 3)) == ()


def test_first_partial_minute_after_connect_is_not_stored():
    aggregator = MinuteAggregator()
    # 9:01:30에 연결이 붙었다. 9:01 분은 앞부분이 비었는지 알 수 없다.
    aggregator.mark_connected(datetime(2026, 8, 18, 9, 1, 30, tzinfo=KST))
    aggregator.add(tick(minute=1, second=40))
    aggregator.add(tick(minute=2, second=5))

    bars = aggregator.flush_before(boundary(9, 3))

    assert [bar.bar_at.astimezone(KST).minute for bar in bars] == [2]
    assert aggregator.skipped_partial_count == 1


def test_reconnect_drops_the_open_minute():
    aggregator = MinuteAggregator()
    aggregator.add(tick(minute=1))

    aggregator.drop_open_minutes()

    assert aggregator.dropped_open_count == 1
    assert aggregator.flush_before(boundary(9, 2)) == ()


def test_krx_and_nxt_series_never_mix():
    aggregator = MinuteAggregator()
    aggregator.add(tick(exchange=StockExchange.KRX, price="100"))
    aggregator.add(tick(exchange=StockExchange.NXT, price="200"))

    bars = aggregator.flush_before(boundary(9, 2))

    assert {(bar.exchange, bar.open) for bar in bars} == {
        (StockExchange.KRX, Decimal(100)),
        (StockExchange.NXT, Decimal(200)),
    }


# ---------------------------------------------------------------- 세션 창·설정


def test_krx_session_bounds():
    day = datetime(2026, 8, 18, tzinfo=KST)

    assert not in_session(StockExchange.KRX, day.replace(hour=8, minute=59, second=59))
    assert in_session(StockExchange.KRX, day.replace(hour=9, minute=0, second=0))
    # 15:30 분(마감 단일가)은 포함이고 15:31부터는 시간외라 버린다.
    assert in_session(StockExchange.KRX, day.replace(hour=15, minute=30, second=59))
    assert not in_session(StockExchange.KRX, day.replace(hour=15, minute=31, second=0))


def test_nxt_uses_the_same_single_window_as_rest():
    day = datetime(2026, 8, 18, tzinfo=KST)

    assert not in_session(StockExchange.NXT, day.replace(hour=7, minute=59))
    assert in_session(StockExchange.NXT, day.replace(hour=8, minute=0))
    # 세션 사이 공백(예: 08:55)도 창 안이다. 체결이 없어 봉이 안 생길 뿐이고,
    # REST 일별 수집과 같은 범위여야 WS에만 구멍이 생기지 않는다.
    assert in_session(StockExchange.NXT, day.replace(hour=8, minute=55))
    assert in_session(StockExchange.NXT, day.replace(hour=20, minute=0, second=59))
    assert not in_session(StockExchange.NXT, day.replace(hour=20, minute=1))


def test_connect_window_is_weekday_0750_to_2010():
    tuesday = datetime(2026, 8, 18, tzinfo=KST)
    saturday = datetime(2026, 8, 22, tzinfo=KST)

    assert not in_connect_window(tuesday.replace(hour=7, minute=49))
    assert in_connect_window(tuesday.replace(hour=7, minute=50))
    assert in_connect_window(tuesday.replace(hour=20, minute=9))
    assert not in_connect_window(tuesday.replace(hour=20, minute=10))
    assert not in_connect_window(saturday.replace(hour=10, minute=0))


def test_settings_reject_mock_domains():
    with pytest.raises(ValueError, match="sandbox"):
        make_settings(rest_domain="https://openapivts.koreainvestment.com:29443")
    with pytest.raises(ValueError, match="sandbox"):
        make_settings(websocket_domain="ws://ops.koreainvestment.com:31000")


def test_websocket_url_appends_tryitout_only_without_path():
    bare = make_settings(websocket_domain="ws://ops.koreainvestment.com:21000")
    with_path = make_settings(websocket_domain="ws://ops.koreainvestment.com:21000/tryitout")

    assert bare.websocket_url() == "ws://ops.koreainvestment.com:21000/tryitout"
    assert with_path.websocket_url() == "ws://ops.koreainvestment.com:21000/tryitout"


def test_registry_honors_the_nxt_flag():
    krx_only = build_registry(make_settings(enable_nxt=False))
    both = build_registry(make_settings())

    assert {(sub.tr_id, sub.tr_key) for sub in krx_only} == {(KRX_TR_ID, "005930"), (KRX_TR_ID, "000660")}
    assert len(both) == 4
    assert {sub.tr_id for sub in both} == {KRX_TR_ID, NXT_TR_ID}


def test_nxt_is_on_by_default():
    """REST 손잡이와 방향을 맞춘 값이다. 기본이 갈리면 한쪽만 켠 사람이 둘 다 켰다고 믿는다."""
    assert make_settings().enable_nxt is True


# ---------------------------------------------------- Airflow 수집기와의 정합


def test_domestic_stocks_match_the_airflow_collector():
    """의도적 중복(백엔드는 airflow 트리를 import하지 않는다)이 어긋나면 여기서 잡는다."""
    from modules.collectors import kis

    assert {member.value for member in DomesticStock} == {member.value for member in kis.DomesticStock}


def test_the_nxt_flags_share_their_default_and_vocabulary():
    """의도적 중복이다. 두 손잡이가 다르게 동작하면 한쪽을 끈 사람이 다른 쪽도 껐다고 믿는다."""
    from apps.realtime import main
    from modules.collectors import kis

    assert main.FLAG_ON_VALUES == kis.FLAG_ON_VALUES
    assert main.FLAG_OFF_VALUES == kis.FLAG_OFF_VALUES
    # 비어 있으면 둘 다 NXT를 켠다.
    assert main.nxt_websocket_enabled() is True
    assert kis.StockExchange.NXT in kis.rest_exchanges()


def test_the_websocket_flag_rejects_an_unknown_value(monkeypatch):
    from apps.realtime import main

    monkeypatch.setenv(main.NXT_WEBSOCKET_FLAG, "fasle")

    with pytest.raises(ValueError, match=main.NXT_WEBSOCKET_FLAG):
        main.nxt_websocket_enabled()


def test_the_websocket_flag_can_still_drop_nxt(monkeypatch):
    from apps.realtime import main

    monkeypatch.setenv(main.NXT_WEBSOCKET_FLAG, "false")

    assert main.nxt_websocket_enabled() is False


def test_session_windows_match_the_airflow_collector():
    # REST 일별 수집과 창이 어긋나면 WS에만 구멍이 생긴다.
    from modules.collectors import kis

    for exchange in (StockExchange.KRX, StockExchange.NXT):
        collector_exchange = kis.StockExchange(exchange.value)
        assert SESSION_WINDOWS[exchange] == (collector_exchange.first_bar, collector_exchange.last_bar)


# ---------------------------------------------------------------- 저장 계약


def sample_row(**overrides):
    row = {
        "provider": "kis",
        "stock_code": "005930",
        "exchange": StockExchange.KRX,
        "bar_at": datetime(2026, 8, 18, 0, 1, tzinfo=UTC),
        "open": Decimal(153000),
        "high": Decimal(153500),
        "low": Decimal(152900),
        "close": Decimal(153100),
        "volume": 22,
        "previous_close": Decimal(150000),
        "ingest_method": "websocket",
        "is_final": False,
        "source_record_id": 77,
    }
    row.update(overrides)
    return row


def test_provisional_upsert_guards_final_rows():
    from sqlalchemy.dialects import postgresql

    statement = repository_module.provisional_upsert([sample_row()])
    sql = str(statement.compile(dialect=postgresql.dialect()))

    # 자연키 충돌 시에만 갱신하고, REST 확정행은 절대 되돌리지 않는다.
    assert "ON CONFLICT ON CONSTRAINT uq_stock_bar_natural_key" in sql
    assert "WHERE stock_bar.is_final IS false" in sql
    assert "ingest_method" in sql
    assert "is_final" in sql


def test_provisional_upsert_rows_cover_required_columns():
    from apps.models.market import StockBar

    required = {
        column.name
        for column in StockBar.__table__.columns
        if not column.nullable and column.server_default is None and not column.primary_key
    }
    assert required <= set(sample_row().keys())


# ---------------------------------------------------------------- 비동기 흐름


class FakeRepository:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self.closed: list[tuple] = []

    async def open_session(self, started_at, metadata):
        return 77

    async def store_bars(self, rows):
        self.rows.extend(rows)
        return len(rows)

    async def close_session(self, source_record_id, completed_at, status, record_count, metadata):
        self.closed.append((source_record_id, status, record_count, metadata))

    async def previous_close(self, stock_code, business_date):
        return Decimal(150000)


class FakeSocket:
    """스크립트된 프레임을 흘리는 웹소켓. 소진되면 정상 종료처럼 닫힌다."""

    def __init__(self, frames: list[str]) -> None:
        self.frames = list(frames)
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, message: str) -> None:
        self.sent.append(message)

    async def recv(self) -> str:
        if not self.frames:
            raise StopAsyncIteration
        return self.frames.pop(0)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        if not self.frames:
            raise StopAsyncIteration
        return self.frames.pop(0)


def ack(tr_id: str, tr_key: str, ok: bool = True) -> str:
    return json.dumps(
        {
            "header": {"tr_id": tr_id, "tr_key": tr_key},
            "body": {"rt_cd": "0" if ok else "1", "msg_cd": "OPSP0000" if ok else "OPSP0011", "msg1": "x"},
        }
    )


@pytest.mark.asyncio
async def test_connection_echoes_pingpong_and_closes_the_session(monkeypatch, tmp_path):
    pingpong = json.dumps({"header": {"tr_id": "PINGPONG", "datetime": "20260818100000"}})
    socket = FakeSocket(
        [
            ack(KRX_TR_ID, "005930"),
            ack(KRX_TR_ID, "000660"),
            pingpong,
            data_frame(KRX_TR_ID, [trade(hour="100001")]),
        ]
    )
    monkeypatch.setattr(service, "connect", lambda url, ping_interval=None: socket)
    # 이 테스트가 보는 것은 프레임 왕복이라 구독은 KRX 둘로 고정한다.
    settings = make_settings(enable_nxt=False)
    repository = FakeRepository()
    heartbeat = heartbeat_module.Heartbeat(tmp_path / "heartbeat.json")

    with pytest.raises(service._StreamEnded):
        await service.KisConnection(
            settings, build_registry(settings), repository, SecretStr("approval"), heartbeat
        ).run()

    # 구독 2건 + PINGPONG 회신 1건. 받은 프레임을 그대로 되돌린다.
    assert len(socket.sent) == 3
    assert socket.sent[2] == pingpong
    (source_record_id, status, record_count, metadata) = repository.closed[0]
    assert (source_record_id, status, record_count) == (77, SourceStatus.SUCCEEDED, 0)
    # 10:00 분은 열린 채 끝났으므로 저장 없이 폐기된다.
    assert metadata["dropped_open_bars"] == 1
    assert repository.rows == []


@pytest.mark.asyncio
async def test_all_rejected_subscriptions_mean_an_auth_problem(monkeypatch, tmp_path):
    socket = FakeSocket([ack(KRX_TR_ID, "005930", ok=False), ack(KRX_TR_ID, "000660", ok=False)])
    monkeypatch.setattr(service, "connect", lambda url, ping_interval=None: socket)
    # 이 테스트가 보는 것은 프레임 왕복이라 구독은 KRX 둘로 고정한다.
    settings = make_settings(enable_nxt=False)
    repository = FakeRepository()
    heartbeat = heartbeat_module.Heartbeat(tmp_path / "heartbeat.json")

    with pytest.raises(AuthRejectedError):
        await service.KisConnection(
            settings, build_registry(settings), repository, SecretStr("approval"), heartbeat
        ).run()

    assert repository.closed[0][1] == SourceStatus.FAILED



def _connection(
    repository,
    heartbeat,
    *,
    aggregator=None,
    previous_closes=None,
    clock=None,
    sleeper=None,
    source_record_id: int = 77,
):
    """flush·watchdog만 보는 테스트용 `KisConnection`. 소켓은 열지 않는다."""
    settings = make_settings(enable_nxt=False)
    connection = service.KisConnection(
        settings,
        build_registry(settings),
        repository,
        SecretStr("approval"),
        heartbeat,
        clock=clock or (lambda: datetime.now(UTC)),
        sleeper=sleeper or asyncio.sleep,
    )
    if aggregator is not None:
        connection._aggregator = aggregator
    if previous_closes is not None:
        connection._previous_closes = dict(previous_closes)
    connection._source_record_id = source_record_id
    return connection


@pytest.mark.asyncio
async def test_flush_timer_stores_bars_after_the_delay(tmp_path):
    aggregator = MinuteAggregator()
    aggregator.mark_connected(datetime(2026, 8, 18, 9, 0, 0, tzinfo=KST))
    aggregator.add(tick(minute=2, second=10))
    repository = FakeRepository()
    heartbeat = heartbeat_module.Heartbeat(tmp_path / "heartbeat.json")
    now = datetime(2026, 8, 18, 9, 2, 40, tzinfo=KST).astimezone(UTC)
    slept: list[float] = []

    async def sleeper(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) > 1:
            raise asyncio.CancelledError

    connection = _connection(
        repository, heartbeat, aggregator=aggregator, previous_closes={"005930": Decimal(150000)},
        clock=lambda: now, sleeper=sleeper,
    )
    counters = connection._counters

    with pytest.raises(asyncio.CancelledError):
        await connection._flush_timer()

    # 9:02:40 기준 다음 경계는 9:03:00, 지연 3초를 더해 23초를 기다린다.
    assert slept[0] == pytest.approx(23.0)
    assert counters["stored_bars"] == 1
    assert len(repository.rows) == 1
    row = repository.rows[0]
    assert row["provider"] == "kis"
    assert row["stock_code"] == "005930"
    assert row["exchange"] is StockExchange.KRX
    assert row["bar_at"] == datetime(2026, 8, 18, 9, 2, tzinfo=KST).astimezone(UTC)
    assert (row["ingest_method"], row["is_final"]) == ("websocket", False)
    assert (row["previous_close"], row["source_record_id"]) == (Decimal(150000), 77)


@pytest.mark.asyncio
async def test_flush_timer_skips_series_without_a_previous_close(tmp_path):
    aggregator = MinuteAggregator()
    aggregator.add(tick(minute=2))
    repository = FakeRepository()
    heartbeat = heartbeat_module.Heartbeat(tmp_path / "heartbeat.json")
    counters: dict[str, int] = {}

    async def sleeper(seconds: float) -> None:
        if counters["skipped_no_previous_close"]:
            raise asyncio.CancelledError

    connection = _connection(
        repository, heartbeat, aggregator=aggregator, previous_closes={},
        clock=lambda: datetime(2026, 8, 18, 9, 2, 40, tzinfo=KST).astimezone(UTC), sleeper=sleeper,
    )
    counters = connection._counters

    with pytest.raises(asyncio.CancelledError):
        await connection._flush_timer()

    assert counters["skipped_no_previous_close"] == 1
    assert repository.rows == []


@pytest.mark.asyncio
async def test_watchdog_raises_when_frames_go_stale():
    now = datetime(2026, 8, 18, 10, 0, 0, tzinfo=KST).astimezone(UTC)
    last_seen = now - timedelta(seconds=service.STALE_FRAME_SECONDS + 1)

    async def sleeper(seconds: float) -> None:
        return None

    connection = _connection(FakeRepository(), None, clock=lambda: now, sleeper=sleeper)
    connection._last_frame_at = last_seen

    with pytest.raises(service.StaleConnectionError):
        await connection._watchdog()


@pytest.mark.asyncio
async def test_watchdog_stops_at_the_connect_window_edge():
    after_close = datetime(2026, 8, 18, 20, 11, 0, tzinfo=KST).astimezone(UTC)

    async def sleeper(seconds: float) -> None:
        return None

    connection = _connection(FakeRepository(), None, clock=lambda: after_close, sleeper=sleeper)
    connection._last_frame_at = after_close

    with pytest.raises(service.ConnectWindowClosed):
        await connection._watchdog()


# ---------------------------------------------------------------- heartbeat


def test_healthcheck_reads_the_heartbeat_file(tmp_path):
    path = Path(tmp_path) / "heartbeat.json"
    heartbeat_module.write_heartbeat(path, "ready", frames=10)

    assert heartbeat_module.healthcheck(path) == 0

    heartbeat_module.write_heartbeat(path, "failed")
    assert heartbeat_module.healthcheck(path) == 1
    assert heartbeat_module.healthcheck(Path(tmp_path) / "missing.json") == 1


def test_healthcheck_flags_a_stale_heartbeat(tmp_path):
    path = Path(tmp_path) / "heartbeat.json"
    heartbeat_module.write_heartbeat(path, "ready")

    later = datetime.now(UTC) + timedelta(seconds=heartbeat_module.HEARTBEAT_STALE_SECONDS + 1)
    assert heartbeat_module.healthcheck(path, now=later) == 1


def test_heartbeat_extra_covers_every_session_counter():
    """카운터를 늘리고 모델을 안 고치면 heartbeat 파일에서 그 값이 조용히 빠진다."""
    fields = set(service.HeartbeatExtra.model_fields)

    assert set(service.SESSION_COUNTERS) <= fields
    assert fields == set(service.SESSION_COUNTERS) | {"session_id", "late_ticks"}


def test_heartbeat_file_carries_the_whole_counter_set(tmp_path):
    """예전에는 `**counters`가 그대로 펼쳐져 키 집합이 코드 어디에도 안 남았다."""
    path = tmp_path / "heartbeat.json"
    heartbeat = heartbeat_module.Heartbeat(path)
    extra = service.HeartbeatExtra(session_id="s1", **service.SESSION_COUNTERS, late_ticks=2)

    heartbeat.update("ready", **extra.model_dump(mode="json"))

    payload = json.loads(path.read_text())
    assert set(payload) == set(service.HeartbeatExtra.model_fields) | {"state", "written_at"}
    assert payload["late_ticks"] == 2
