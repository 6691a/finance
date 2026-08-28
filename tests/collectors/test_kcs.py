import re
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Self

import pytest
from pydantic import SecretStr, ValidationError

from modules.collectors.indicator.kcs import (
    ALL_SERIES,
    COLUMN_COUNT,
    DATASETS,
    FIRST_MONTH,
    MAX_MONTHS,
    OBSERVATION_UPSERT,
    SOURCE,
    SOURCE_RECORD_INSERT,
    UNIT,
    KcsDataset,
    KcsPayloadError,
    KcsRequest,
    KcsResponse,
    KcsResultError,
    KcsTradeCollector,
    parse_items,
)

SOURCE_RECORD_ID = 7
SERVICE_KEY = SecretStr("k" * 40)
COLLECTOR = KcsTradeCollector(SERVICE_KEY)
STARTED_AT = datetime(2026, 8, 28, 0, 30, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 28, 0, 30, 1, tzinfo=UTC)


def item_xml(period_month: str, period_days: str, first: str = "          29,827,757") -> str:
    """관세청 응답의 행 하나. 값은 콤마와 좌측 공백이 붙은 문자열로 온다."""
    columns = "".join(
        f"<itemUsdAmt{index:02d}>{first if index == 0 else f'           1,000,00{index % 10}'}</itemUsdAmt{index:02d}>"
        for index in range(COLUMN_COUNT)
    )
    return (
        f"<item>{columns}"
        f"<priodDt>{period_days}</priodDt>"
        f"<priodMon>{period_month}</priodMon>"
        f"<priodYear>{period_month[:4]}</priodYear></item>"
    )


def body_for(*items: str, code: str = "00", message: str = "정상서비스.", total: int | None = None) -> bytes:
    count = len(items) if total is None else total
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f"<response><header><resultCode>{code}</resultCode><resultMsg>{message}</resultMsg></header>"
        f"<body><items>{''.join(items)}</items><totalCount>{count}</totalCount></body></response>"
    ).encode()


PAYLOAD = body_for(
    item_xml("202607", "01~10"),
    item_xml("202607", "01~20"),
    item_xml("202607", "01~31"),
)


def request_for(
    dataset: KcsDataset = KcsDataset.EXPORT_ITEM,
    start: str = "202607",
    end: str = "202607",
) -> KcsRequest:
    return KcsRequest(dataset=dataset, start_month=start, end_month=end)


def response_for(body: bytes = PAYLOAD, request: KcsRequest | None = None) -> KcsResponse:
    return KcsResponse(
        request=request or request_for(),
        body=body,
        status=200,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple) -> None:
        self.calls.append((statement, parameters))

    def fetchone(self) -> tuple[int]:
        return (SOURCE_RECORD_ID,)


class FakeConnection:
    def __init__(self) -> None:
        self.recorded_cursor = FakeCursor()

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


def test_the_request_window_is_months_not_days():
    """제공처가 `YYYYMM`을 받는다. 하루라도 걸친 달은 통째로 받아야 그 달의 누계 행이 들어온다."""
    request = KcsRequest.from_dates(KcsDataset.EXPORT_ITEM, date(2026, 5, 20), date(2026, 8, 3))

    assert (request.start_month, request.end_month) == ("202605", "202608")
    assert request.month_count == 4


@pytest.mark.parametrize("month", ["2026-07", "20267", "202613", "202600", "abcdef"])
def test_a_malformed_month_is_refused_before_the_call(month):
    with pytest.raises(ValidationError):
        KcsRequest(dataset=KcsDataset.EXPORT_ITEM, start_month=month, end_month="202607")


def test_a_reversed_window_is_refused():
    with pytest.raises(ValidationError):
        request_for(start="202607", end="202601")


def test_an_unknown_dataset_is_refused():
    # 데이터셋 이름을 문자열로 넘기는 자리는 DAG의 태스크 매핑뿐이다. 오타가 요청 전에 죽어야 한다.
    with pytest.raises(ValueError, match="not a valid"):
        KcsDataset("export_items")


def test_a_window_longer_than_the_provider_allows_is_refused_before_the_call():
    """제공처도 본문 오류로 막지만 요청 전에 막는다. 백필이 이 상한에 걸린다(2026-08-28 실측)."""
    assert request_for(start="201609", end="202608").month_count == MAX_MONTHS

    with pytest.raises(ValidationError):
        request_for(start="201608", end="202608")


@pytest.mark.parametrize("dataset", list(KcsDataset))
def test_each_dataset_calls_its_own_operation(dataset):
    """경로와 오퍼레이션 이름이 대문자 하나만 다르다(2026-08-28 실측)."""
    url = COLLECTOR.build_url(request_for(dataset))

    assert f"/1220000/{dataset.stem}/get{dataset.stem[0].upper()}{dataset.stem[1:]}?" in url
    assert "strtYymm=202607" in url
    assert "endYymm=202607" in url


def test_the_service_key_goes_into_the_url():
    assert SERVICE_KEY.get_secret_value() in COLLECTOR.build_url(request_for())


def test_an_encoded_key_is_not_encoded_twice():
    """포털이 Encoding·Decoding 두 형태로 키를 준다. 원문을 담아야 `%2F`가 `%252F`가 되지 않는다."""
    collector = KcsTradeCollector(SecretStr("ab/cd+ef=="))

    assert "ab%2Fcd%2Bef%3D%3D" in collector.build_url(request_for())


def test_every_row_becomes_one_observation_per_series():
    observations = parse_items(PAYLOAD, request_for())

    assert len(observations) == 3 * len(KcsDataset.EXPORT_ITEM.series)
    assert {observation.observation_date for observation in observations} == {
        date(2026, 7, 10),
        date(2026, 7, 20),
        date(2026, 7, 31),
    }


def test_the_amount_loses_its_commas_and_padding():
    observations = parse_items(PAYLOAD, request_for())
    total = next(
        observation
        for observation in observations
        if observation.series_id == "KR_EXPORT_MTD" and observation.observation_date == date(2026, 7, 10)
    )

    assert total.value == Decimal(29827757)


def test_february_keeps_its_own_last_day():
    """말일이 달마다 다르다. 윤년 2월은 `01~29`로 온다(2026-08-28 실측)."""
    leap = parse_items(body_for(item_xml("202402", "01~29")), request_for(start="202402", end="202402"))
    common = parse_items(body_for(item_xml("202602", "01~28")), request_for(start="202602", end="202602"))

    assert leap[0].observation_date == date(2024, 2, 29)
    assert common[0].observation_date == date(2026, 2, 28)


@pytest.mark.parametrize("period", ["01~15", "01~30", "11~20", "01-10", ""])
def test_a_period_that_is_not_a_known_cutoff_fails(period):
    # 2026-02에 `01~30`은 없는 날이고 `01~15`는 우리가 모르는 마감이다. 조용히 엉뚱한 날짜로
    # 저장하는 것보다 멈추는 편이 낫다.
    with pytest.raises(KcsPayloadError):
        parse_items(body_for(item_xml("202602", period)), request_for(start="202602", end="202602"))


def test_a_truncated_response_fails():
    # 제공처가 알려 준 건수와 받은 행 수를 대조한다. 조용히 잘린 응답은 구간에 구멍을 남긴다.
    with pytest.raises(KcsPayloadError):
        parse_items(body_for(item_xml("202607", "01~10"), total=3), request_for())


def test_a_month_outside_the_requested_window_fails():
    with pytest.raises(KcsPayloadError):
        parse_items(body_for(item_xml("202606", "01~10")), request_for())


@pytest.mark.parametrize("dataset", list(KcsDataset))
def test_a_missing_column_fails_even_when_that_column_is_not_stored(dataset):
    """국가별은 `00`(전체)을 버리지만 그 칸이 사라진 것도 응답 모양이 바뀐 것이다.

    칸이 하나 줄면 그 뒤가 전부 한 칸씩 밀려 다른 나라 값이 저장된다.
    """
    broken = re.sub(r"<itemUsdAmt00>.*?</itemUsdAmt00>", "", item_xml("202607", "01~10"))

    with pytest.raises(KcsPayloadError):
        parse_items(body_for(broken), request_for(dataset))


def test_a_non_numeric_amount_fails():
    broken = item_xml("202607", "01~10", first="  -  ")

    with pytest.raises(KcsPayloadError):
        parse_items(body_for(broken), request_for())


def test_a_body_level_failure_becomes_its_own_error():
    """제공처가 실패를 HTTP 상태가 아니라 본문으로 알린다(2026-08-28 실측: HTTP 200 + code 99)."""
    with pytest.raises(KcsResultError) as failure:
        parse_items(body_for(code="99", message="필수 요청변수가 누락되었습니다."), request_for())

    # 코드를 해석하지 않고 그대로 올린다. 재시도 여부는 DAG가 정한다.
    assert failure.value.code == "99"


def test_a_body_that_is_not_xml_fails():
    with pytest.raises(KcsPayloadError):
        parse_items(b"<not-xml", request_for())


def test_store_writes_one_source_record_and_every_observation():
    connection = FakeConnection()

    count = COLLECTOR.store_observations(connection, response_for())

    calls = connection.recorded_cursor.calls
    assert count == 3 * len(KcsDataset.EXPORT_ITEM.series)
    assert calls[0][0] == SOURCE_RECORD_INSERT
    assert len(calls) == 1 + count
    assert all(statement == OBSERVATION_UPSERT for statement, _ in calls[1:])


def test_the_source_record_names_the_dataset():
    """조회 한 번이 레코드 한 건이다. 어느 데이터셋이었는지가 `source_key`에 남는다."""
    connection = FakeConnection()

    COLLECTOR.store_observations(connection, response_for(request=request_for(KcsDataset.IMPORT_COUNTRY)))

    _, parameters = connection.recorded_cursor.calls[0]
    assert parameters[1:3] == (SOURCE, "import_country_tenday")
    assert parameters[5] == "succeeded"


def test_the_xml_body_is_not_stored_as_payload():
    """`payload` 컬럼 타입이 jsonb인데 원본이 XML이다. 데이터셋과 구간은 metadata가 갖는다."""
    connection = FakeConnection()

    COLLECTOR.store_observations(connection, response_for())

    _, parameters = connection.recorded_cursor.calls[0]
    assert parameters[7] is None
    assert '"start_month": "202607"' in parameters[8]
    assert '"service": "prlstMmUtPrviExpAcrs"' in parameters[8]


def test_every_observation_carries_the_declared_unit():
    connection = FakeConnection()

    COLLECTOR.store_observations(connection, response_for())

    for _, parameters in connection.recorded_cursor.calls[1:]:
        assert parameters[0] == SOURCE
        assert parameters[4] == UNIT


def test_nothing_is_written_when_the_payload_is_broken():
    # 파싱을 먼저 해서 형식 오류면 아무 것도 쓰지 않는다.
    connection = FakeConnection()

    with pytest.raises(KcsPayloadError):
        COLLECTOR.store_observations(connection, response_for(body=b"<not-xml"))

    assert connection.recorded_cursor.calls == []


@pytest.mark.parametrize("dataset", list(KcsDataset))
def test_every_dataset_reads_columns_inside_the_response_shape(dataset):
    """열 번호가 곧 항목이다. 번호가 겹치거나 범위를 벗어나면 값이 다른 항목에 붙는다."""
    columns = [series.column for series in dataset.series]

    assert len(set(columns)) == len(columns)
    assert all(0 <= column < COLUMN_COUNT for column in columns)
    assert columns == sorted(columns)


def test_only_the_item_datasets_store_the_overall_total():
    """국가별 전체는 품목별과 같은 값이다(2026-07 1~10일 둘 다 29,827,757).

    같은 자연키에 두 번 쓰면 계보만 흐려지므로 국가별은 `00`을 버린다.
    """
    stores_total = {dataset for dataset in KcsDataset if any(series.column == 0 for series in dataset.series)}

    assert stores_total == {KcsDataset.EXPORT_ITEM, KcsDataset.IMPORT_ITEM}


def test_export_and_import_countries_are_not_assumed_to_match():
    """나라 목록과 순서가 방향마다 다르다(2026-08-28 명세 실측).

    수출에는 홍콩·인도·싱가포르가, 수입에는 호주·사우디아라비아·러시아연방이 있고 일본과
    베트남의 자리도 바뀐다. 한쪽을 복사하면 값이 통째로 다른 나라에 붙는다.
    """
    export = {series.column: series.series_id for series in KcsDataset.EXPORT_COUNTRY.series}
    imports = {series.column: series.series_id for series in KcsDataset.IMPORT_COUNTRY.series}

    assert export[4] == "KR_EXPORT_VN_MTD"
    assert imports[4] == "KR_IMPORT_JP_MTD"
    assert "KR_EXPORT_HK_MTD" in export.values()
    assert "KR_IMPORT_AU_MTD" in imports.values()


def test_series_identifiers_are_unique_across_datasets():
    # 넷이 한 테이블에 쌓인다. 겹치면 한 데이터셋이 다른 데이터셋의 값을 덮는다.
    assert len(set(ALL_SERIES)) == len(ALL_SERIES)
    assert len(ALL_SERIES) == 11 + 11 + 10 + 10


def test_every_series_says_it_is_a_month_to_date_total():
    # 값이 월초부터의 누계다. 표시가 없으면 매월 셋이 커졌다 떨어지는 그림을 급감으로 읽는다.
    assert all(series_id.endswith("_MTD") for series_id in ALL_SERIES)


def test_the_dag_maps_over_every_dataset():
    assert set(DATASETS) == {dataset.value for dataset in KcsDataset}
    assert len(DATASETS) == 4


def test_the_first_month_the_provider_serves_is_recorded():
    # 2015-12는 정상 응답에 0건으로 답한다(2026-08-28 실측). 백필 구간의 시작이 이 값이다.
    assert FIRST_MONTH == "201601"
