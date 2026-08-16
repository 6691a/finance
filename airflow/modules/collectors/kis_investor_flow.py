"""한국투자증권 API에서 외국인·기관·개인 수급을 수집한다.

세 조회를 담는다. 장중 종목 추정, 장중 시장 누적, 그리고 장 마감 뒤 종목 확정 일별이다.

가격이 "얼마에 거래됐나"이고 포지션이 "누가 들고 있나"라면, 수급은 **"지금 누가 사고 누가
파나"**다. 지수가 오르는데 외국인이 팔고 개인이 받는 장과, 외국인이 사는 장은 다음 날이
다르다.

토큰 발급과 HTTP는 `kis.py`가 갖고 있어 그대로 쓴다. `kis_market_calendar.py`·
`kis_positioning.py`와 같은 이유로 모듈을 나눴다.

저장 대상은 `stock_investor_estimate_snapshot`, `market_investor_flow_snapshot`,
`stock_investor_trade_daily`다. 정의의 원본은 백엔드의 `apps/models/market.py`이며 여기 SQL의
컬럼 이름은 `tests/collectors/test_kis_investor_flow.py`가 그 모델 metadata와 대조한다.

아래 계약은 2026-08-14 장중에 운영 키로 확인했다.

## 두 API의 성격이 다르다

| | 종목 추정 | 시장 누적 |
| --- | --- | --- |
| 값 | 장중 **추정치** | 장중 누적 스냅샷 |
| 응답 | 갱신 슬롯마다 한 행 | 한 행 |
| 원천 시각 | 없다. 슬롯 코드뿐 | 없다 |
| 투자자 | 외국인·기관 | 72필드. 12개 분류를 저장한다 |
| 단위 | 주로 보인다 | **주·원이 아니다**(§단위) |

세 번째 조회인 확정 일별(`FHPTJ04160001`)은 성격이 또 다르다. 한 응답이 30 거래일을 담고,
12개 분류가 전부 있으며, 외국인이 등록·미등록으로 갈리고, **단위가 확정돼 있다**
(수량은 주, 투자자별 대금은 백만원). 자세한 것은 `StockTradeDailyRow`와
`fetch_stock_trade_daily`의 문서 문자열에 있다.

## 슬롯이 자연키에 들어간다

종목 응답은 한 번에 여러 행이다. `bsop_hour_gb`는 시각이 아니라 **그날 몇 번째 갱신인지를
뜻하는 회차**이고 최신 슬롯이 먼저 온다. 값은 그 시점까지의 당일 누적이라 슬롯이 커질수록
쌓이며, 장이 진행되면 행이 늘어난다(실측: 10:44에 2행, 14:43에 5행).

```text
005930  gb=2  외국인   878,000  기관  -464,000  합   414,000
005930  gb=1  외국인 1,059,000  기관         0  합 1,059,000
```

수집 시각을 키로 쓰면 이 두 행이 같은 분에 몰려 하나만 남는다. **슬롯 코드를 시각으로
환산하지도 않는다.** 공식 예제가 갱신 시각이 변동될 수 있다고 밝히고 있어, 표를 만들면 그
표가 틀리는 날 조용히 어긋난다.

## 잘못된 시장 코드가 0으로 온다

코스닥 코드를 찾으려 후보를 넣어 봤더니 `999/S002`, `999/K001`, `999/Q001`, `999/S003`,
`998/S001`, `1001/S001`이 **전부 `rt_cd=0`에 값이 모두 0**이었다. 오류가 아니다. ECOS가
없는 항목코드에 `INFO-200`을 주던 것과 같은 함정이다.

그래서 두 가지를 함께 건다. 시장 코드는 이 모듈의 Enum이 정한 것만 보내고, **모든 값이 0인
응답은 저장하지 않고 실패시킨다.** 장중에 전 투자자 분류가 정확히 0인 일은 없다.

**코스닥은 아직 켜지 않는다.** 근거 있는 코드를 얻기 전까지 Enum에 넣지 않는다.

## 시장 응답의 12개 분류와 두 항등식

한 응답에 12개 투자자 분류가 온다. 상위 셋(외국인·기관계·개인)은 매도·매수·순매수·대금을
모두 저장하고, 기관 세부 일곱과 기타 둘은 순매수 수량만 저장한다. 그쪽에서 필요한 것은
방향이고 대금은 배율이 미확정이라 지금 넣어도 읽을 수 없다.

```text
기관계 = 금융투자 + 투자신탁 + 사모펀드 + 은행 + 보험 + 종금 + 기금
개인 + 외국인 + 기관계 + 기타법인 + 기타단체 = 0
```

둘 다 실측으로 정확히 성립했고 `MarketFlowRow.from_payload`가 매 응답 검증한다. 시장 전체는
닫혀 있어서 누가 팔면 누군가는 받는다. 닫히지 않으면 분류를 빠뜨렸거나 필드를 잘못 읽은
것이다.

**접미사가 분류마다 다르다.** 사모펀드·기타법인·기타단체만 `_ntby_vol`이고 나머지는
`_ntby_qty`다. `f"{prefix}_ntby_qty"` 한 벌로 조립하면 그 셋이 오류 없이 0이 된다.

## 단위

시장 응답의 총매도 대금을 수량으로 나누면 2.5~3.4가 나온다. 주·원이라면 평균단가가 3원이라는
뜻이라 그럴 수 없다. `천주`·`백만원`이면 2,966원이 되어 값이 맞지만 확정하지 못했다. 반면
종목 추정의 878,000은 주 단위로 보인다. **두 API의 배율이 다르다는 것만 확실하다.** 저장은
표기 그대로 두고 화면이 두 값을 같은 축에 그리지 않는다.
"""

import json
import logging
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from modules.collectors.kis import (
    SOURCE,
    Connection,
    KisPayloadError,
    KisResultError,
    send_get,
)
from modules.sql import read_sql
from modules.upsert import execute_upserts

logger = logging.getLogger(__name__)

STOCK_ESTIMATE_PATH = "/uapi/domestic-stock/v1/quotations/investor-trend-estimate"
STOCK_ESTIMATE_TR_ID = "HHPTJ04160200"
STOCK_ESTIMATE_SOURCE_KEY = "investor_trend_estimate"

MARKET_FLOW_PATH = "/uapi/domestic-stock/v1/quotations/inquire-investor-time-by-market"
MARKET_FLOW_TR_ID = "FHPTJ04030000"
MARKET_FLOW_SOURCE_KEY = "investor_time_by_market"

DAILY_TRADE_PATH = "/uapi/domestic-stock/v1/quotations/investor-trade-by-stock-daily"
DAILY_TRADE_TR_ID = "FHPTJ04160001"
DAILY_TRADE_SOURCE_KEY = "investor_trade_by_stock_daily"

# 이 조회는 시장 구분 하나로 코스피와 코스닥을 함께 받는다. 장중 시장 조회처럼 시장별 코드를
# 찾을 필요가 없다(실측: 247540이 `J`로 KSQ150 응답).
DAILY_TRADE_MARKET_DIV = "J"

# 한 응답이 담는 거래일 수. `tr_cont`가 빈 문자열로 와서 연속조회가 없다(실측).
DAILY_TRADE_ROWS_PER_CALL = 30

STOCK_ESTIMATE_UPSERT = read_sql("postgres", "stock_investor_estimate_snapshot", "upsert.sql")
DAILY_TRADE_UPSERT = read_sql("postgres", "stock_investor_trade_daily", "upsert.sql")
MARKET_FLOW_UPSERT = read_sql("postgres", "market_investor_flow_snapshot", "upsert.sql")
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")


class InvestorFlowStock(StrEnum):
    """수급을 받을 종목. 값이 한국거래소 6자리 코드다.

    `disclosure_event.stock_code`, `krx_stock_*.stock_code`와 같은 체계라 공시·포지션과 한
    키로 이어진다. `tests/collectors/test_kis_investor_flow.py`가 다른 수집기 Enum과 대조한다.
    """

    label: str

    def __new__(cls, code: str, label: str) -> Self:
        member = str.__new__(cls, code)
        member._value_ = code
        member.label = label
        return member

    SAMSUNG_ELECTRONICS = ("005930", "삼성전자")
    SK_HYNIX = ("000660", "SK하이닉스")


class InvestorFlowMarket(StrEnum):
    """수급을 받을 시장. 값이 `KrxMarket`과 같고 조회 코드 둘을 함께 든다.

    **확인된 코드만 넣는다.** 잘못된 코드가 오류가 아니라 값 0으로 오기 때문에, 후보를 넣어
    두면 조용히 0이 쌓인다. 코스닥은 근거 있는 코드를 얻은 뒤에 더한다.
    """

    primary_code: str
    secondary_code: str

    def __new__(cls, market: str, primary: str, secondary: str) -> Self:
        member = str.__new__(cls, market)
        member._value_ = market
        member.primary_code = primary
        member.secondary_code = secondary
        return member

    KOSPI = ("KOSPI", "999", "S001")


class StockEstimateRow(BaseModel):
    """종목 추정 응답의 한 슬롯."""

    model_config = ConfigDict(frozen=True)

    source_time_code: str
    foreign_net_buy_qty: int
    institution_net_buy_qty: int
    total_net_buy_qty: int

    @classmethod
    def from_payload(cls, row: dict[str, Any]) -> "StockEstimateRow":
        code = str(row["bsop_hour_gb"]).strip()
        if not code:
            raise KisPayloadError("estimate row has an empty bsop_hour_gb")
        foreign = _int(row.get("frgn_fake_ntby_qty"), "frgn_fake_ntby_qty")
        institution = _int(row.get("orgn_fake_ntby_qty"), "orgn_fake_ntby_qty")
        total = _int(row.get("sum_fake_ntby_qty"), "sum_fake_ntby_qty")
        # 네 행 모두 합이 맞았다(실측). 어긋나면 필드 뜻이 바뀐 것이라 저장하지 않는다.
        if foreign + institution != total:
            raise KisPayloadError(f"estimate sum does not add up on slot {code}: {foreign} + {institution} != {total}")
        return cls(
            source_time_code=code,
            foreign_net_buy_qty=foreign,
            institution_net_buy_qty=institution,
            total_net_buy_qty=total,
        )


# 기관계를 이루는 세부 일곱. **접미사가 분류마다 다르다.** 사모펀드만 `_ntby_vol`이고
# 나머지는 `_ntby_qty`다(실측). 한 벌로 조립하면 그 하나가 조용히 0이 된다.
INSTITUTION_PARTS: tuple[tuple[str, str], ...] = (
    ("securities", "scrt_ntby_qty"),
    ("investment_trust", "ivtr_ntby_qty"),
    ("private_equity", "pe_fund_ntby_vol"),
    ("bank", "bank_ntby_qty"),
    ("insurance", "insu_ntby_qty"),
    ("merchant_bank", "mrbn_ntby_qty"),
    ("pension_fund", "fund_ntby_qty"),
)

# 기관에도 개인에도 들어가지 않는 둘. 이 둘까지 있어야 합이 0으로 닫힌다.
OTHER_PARTS: tuple[tuple[str, str], ...] = (
    ("other_corporation", "etc_corp_ntby_vol"),
    ("other_organization", "etc_orgt_ntby_vol"),
)


class MarketFlowRow(BaseModel):
    """시장 누적 매매동향 한 벌. 12개 투자자 분류를 담는다.

    상위 셋(외국인·기관계·개인)은 매도·매수·순매수·대금을 모두 담고, 기관 세부 일곱과 기타
    둘은 순매수 수량만 담는다. 그쪽에서 필요한 것은 방향이고 대금은 배율이 미확정이라 지금
    넣어도 읽을 수 없다.
    """

    model_config = ConfigDict(frozen=True)

    foreign_sell_qty: int
    foreign_buy_qty: int
    foreign_net_buy_qty: int
    foreign_net_buy_amount: Decimal
    institution_sell_qty: int
    institution_buy_qty: int
    institution_net_buy_qty: int
    institution_net_buy_amount: Decimal
    individual_sell_qty: int
    individual_buy_qty: int
    individual_net_buy_qty: int
    individual_net_buy_amount: Decimal
    securities_net_buy_qty: int
    investment_trust_net_buy_qty: int
    private_equity_net_buy_qty: int
    bank_net_buy_qty: int
    insurance_net_buy_qty: int
    merchant_bank_net_buy_qty: int
    pension_fund_net_buy_qty: int
    other_corporation_net_buy_qty: int
    other_organization_net_buy_qty: int

    @classmethod
    def from_payload(cls, row: dict[str, Any]) -> "MarketFlowRow":
        values: dict[str, Any] = {}
        for name, prefix in (("foreign", "frgn"), ("institution", "orgn"), ("individual", "prsn")):
            sell = _int(row.get(f"{prefix}_seln_vol"), f"{prefix}_seln_vol")
            buy = _int(row.get(f"{prefix}_shnu_vol"), f"{prefix}_shnu_vol")
            net = _int(row.get(f"{prefix}_ntby_qty"), f"{prefix}_ntby_qty")
            # 매수 - 매도 = 순매수. 실측에서 세 분류 모두 맞았다.
            if buy - sell != net:
                raise KisPayloadError(f"{name} net buy does not add up: {buy} - {sell} != {net}")
            values |= {
                f"{name}_sell_qty": sell,
                f"{name}_buy_qty": buy,
                f"{name}_net_buy_qty": net,
                f"{name}_net_buy_amount": _decimal(row.get(f"{prefix}_ntby_tr_pbmn"), f"{prefix}_ntby_tr_pbmn"),
            }

        for name, field in INSTITUTION_PARTS + OTHER_PARTS:
            values[f"{name}_net_buy_qty"] = _int(row.get(field), field)

        parts = sum(values[f"{name}_net_buy_qty"] for name, _ in INSTITUTION_PARTS)
        if parts != values["institution_net_buy_qty"]:
            raise KisPayloadError(f"institution parts do not add up: {parts} != {values['institution_net_buy_qty']}")

        # 시장 전체는 닫혀 있다. 누가 팔면 누군가는 받는다(실측: 정확히 0).
        closed = (
            values["individual_net_buy_qty"]
            + values["foreign_net_buy_qty"]
            + values["institution_net_buy_qty"]
            + values["other_corporation_net_buy_qty"]
            + values["other_organization_net_buy_qty"]
        )
        if closed != 0:
            raise KisPayloadError(f"investor categories do not close to zero: {closed}")

        return cls(**values)

    @property
    def empty(self) -> bool:
        """모든 값이 0인 응답.

        **장중에 전 투자자 분류가 정확히 0인 일은 없다.** 잘못된 시장 코드가 오류가 아니라
        0으로 오기 때문에(실측) 이 상태를 실패로 다룬다.
        """
        return not any(
            (
                self.foreign_sell_qty,
                self.foreign_buy_qty,
                self.institution_sell_qty,
                self.institution_buy_qty,
                self.individual_sell_qty,
                self.individual_buy_qty,
            )
        )


class StockEstimateFetch(BaseModel):
    model_config = ConfigDict(frozen=True)

    stock_code: str
    business_date: date
    rows: tuple[StockEstimateRow, ...]
    started_at: datetime
    completed_at: datetime


class MarketFlowFetch(BaseModel):
    model_config = ConfigDict(frozen=True)

    market_code: str
    observed_at: datetime
    row: MarketFlowRow
    started_at: datetime
    completed_at: datetime


def _int(value: Any, field: str) -> int:
    """수량 한 칸.

    값이 `000000000000878000`, `-00000000000464000`처럼 부호 뒤에 0이 채워져 온다. 파이썬
    `int`가 그대로 읽는다. 음수는 정상값이다.
    """
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text or text == "-":
        return 0
    try:
        return int(text)
    except ValueError:
        raise KisPayloadError(f"KIS returned a non-numeric {field}: {value!r}") from None


def _decimal(value: Any, field: str) -> Decimal:
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text or text == "-":
        return Decimal(0)
    try:
        return Decimal(text)
    except InvalidOperation:
        raise KisPayloadError(f"KIS returned a non-numeric {field}: {value!r}") from None


def _call(
    token: SecretStr,
    app_key: SecretStr,
    app_secret: SecretStr,
    path: str,
    tr_id: str,
    query: dict[str, str],
) -> dict[str, Any]:
    body, _, _ = send_get(token, app_key, app_secret, path, tr_id, query)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise KisPayloadError(f"KIS returned a non-JSON body: {error}") from None
    if not isinstance(payload, dict):
        raise KisPayloadError("KIS returned a JSON body that is not an object")

    code = str(payload.get("rt_cd", ""))
    if code != "0":
        raise KisResultError(code, str(payload.get("msg1", "")).strip())
    return payload


def _rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    output = payload.get(key) or []
    if isinstance(output, dict):
        return [output]
    if not isinstance(output, list):
        raise KisPayloadError(f"KIS returned a {key} that is neither a list nor an object")
    return output


def fetch_stock_estimates(
    token: SecretStr,
    app_key: SecretStr,
    app_secret: SecretStr,
    stock: InvestorFlowStock,
    business_date: date,
) -> StockEstimateFetch:
    """한 종목의 외국인·기관 추정 순매수를 받는다.

    응답에 날짜가 없어 `business_date`를 호출자가 넘긴다. 없는 종목코드는 오류가 아니라
    0행으로 오는데(실측) 종목은 Enum이 막으므로 0행은 정상으로 다룬다.
    """
    started_at = datetime.now(UTC)
    payload = _call(
        token,
        app_key,
        app_secret,
        STOCK_ESTIMATE_PATH,
        STOCK_ESTIMATE_TR_ID,
        {"MKSC_SHRN_ISCD": stock.value},
    )
    raw = _rows(payload, "output2")
    try:
        rows = tuple(StockEstimateRow.from_payload(row) for row in raw)
    except (KeyError, ValidationError) as error:
        raise KisPayloadError(f"KIS estimate row is malformed: {error}") from None

    slots = {row.source_time_code for row in rows}
    if len(slots) != len(rows):
        raise KisPayloadError(f"KIS returned duplicated slots for {stock.value}: {sorted(slots)}")

    return StockEstimateFetch(
        stock_code=stock.value,
        business_date=business_date,
        rows=rows,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


def fetch_market_flow(
    token: SecretStr,
    app_key: SecretStr,
    app_secret: SecretStr,
    market: InvestorFlowMarket,
    observed_at: datetime,
) -> MarketFlowFetch:
    """한 시장의 투자자 누적 매매동향을 받는다.

    **모든 값이 0이면 실패시킨다.** 잘못된 시장 코드가 오류 없이 0을 돌려주기 때문이다.
    `observed_at`은 제공처가 준 시각이 아니라 호출자가 절삭한 수집 시각이다.
    """
    started_at = datetime.now(UTC)
    payload = _call(
        token,
        app_key,
        app_secret,
        MARKET_FLOW_PATH,
        MARKET_FLOW_TR_ID,
        {"FID_INPUT_ISCD": market.primary_code, "FID_INPUT_ISCD_2": market.secondary_code},
    )
    raw = _rows(payload, "output")
    if not raw:
        raise KisPayloadError(f"KIS returned no market flow row for {market.value}")

    try:
        row = MarketFlowRow.from_payload(raw[0])
    except (KeyError, ValidationError) as error:
        raise KisPayloadError(f"KIS market flow row is malformed: {error}") from None

    if row.empty:
        raise KisPayloadError(
            f"KIS returned an all-zero market flow for {market.value} "
            f"({market.primary_code}/{market.secondary_code}); the code is probably wrong"
        )

    return MarketFlowFetch(
        market_code=market.value,
        observed_at=observed_at.astimezone(UTC),
        row=row,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


def _insert_source_record(
    cursor: Any,
    source_key: str,
    started_at: datetime,
    completed_at: datetime,
    record_count: int,
    metadata: dict[str, Any],
) -> int:
    cursor.execute(
        SOURCE_RECORD_INSERT,
        (
            "api",
            SOURCE,
            source_key,
            started_at,
            completed_at,
            "succeeded",
            record_count,
            # 원본은 남기지 않는다. 5분마다 도는 조회라 계보가 값보다 빨리 커진다.
            None,
            json.dumps(metadata, ensure_ascii=False),
        ),
    )
    return cursor.fetchone()[0]


def store_stock_estimates(connection: Connection, fetch: StockEstimateFetch) -> int:
    """슬롯마다 한 행을 저장한다. 같은 슬롯을 다시 받으면 갱신된다."""
    with connection.cursor() as cursor:
        source_record_id = _insert_source_record(
            cursor,
            STOCK_ESTIMATE_SOURCE_KEY,
            fetch.started_at,
            fetch.completed_at,
            len(fetch.rows),
            {
                "stock_code": fetch.stock_code,
                "business_date": fetch.business_date.isoformat(),
                "slots": [row.source_time_code for row in fetch.rows],
            },
        )
        execute_upserts(
            cursor,
            STOCK_ESTIMATE_UPSERT,
            [
                (
                    fetch.stock_code,
                    fetch.business_date,
                    row.source_time_code,
                    row.foreign_net_buy_qty,
                    row.institution_net_buy_qty,
                    row.total_net_buy_qty,
                    fetch.completed_at,
                    source_record_id,
                )
                for row in fetch.rows
            ],
        )
    return len(fetch.rows)


def store_market_flow(connection: Connection, fetch: MarketFlowFetch) -> int:
    """누적 스냅샷 한 분을 저장한다. 델타는 계산하지 않는다."""
    row = fetch.row
    with connection.cursor() as cursor:
        source_record_id = _insert_source_record(
            cursor,
            MARKET_FLOW_SOURCE_KEY,
            fetch.started_at,
            fetch.completed_at,
            1,
            {
                "market_code": fetch.market_code,
                "observed_at": fetch.observed_at.isoformat(),
            },
        )
        cursor.execute(
            MARKET_FLOW_UPSERT,
            (
                fetch.market_code,
                fetch.observed_at,
                row.foreign_sell_qty,
                row.foreign_buy_qty,
                row.foreign_net_buy_qty,
                row.foreign_net_buy_amount,
                row.institution_sell_qty,
                row.institution_buy_qty,
                row.institution_net_buy_qty,
                row.institution_net_buy_amount,
                row.individual_sell_qty,
                row.individual_buy_qty,
                row.individual_net_buy_qty,
                row.individual_net_buy_amount,
                row.securities_net_buy_qty,
                row.investment_trust_net_buy_qty,
                row.private_equity_net_buy_qty,
                row.bank_net_buy_qty,
                row.insurance_net_buy_qty,
                row.merchant_bank_net_buy_qty,
                row.pension_fund_net_buy_qty,
                row.other_corporation_net_buy_qty,
                row.other_organization_net_buy_qty,
                source_record_id,
            ),
        )
    return 1


class StockTradeDailyRow(BaseModel):
    """종목별 투자자 매매동향 확정값 한 거래일.

    장중 추정(`StockEstimateRow`)과 달리 12개 분류가 전부 있고 외국인이 등록·미등록으로
    갈린다. 네 항등식을 `from_payload`가 검증한다.
    """

    model_config = ConfigDict(frozen=True)

    business_date: date
    open_price: Decimal
    high_price: Decimal
    low_price: Decimal
    close_price: Decimal
    accumulated_volume: int
    accumulated_trade_amount: Decimal
    foreign_net_buy_qty: int
    foreign_registered_net_buy_qty: int
    foreign_unregistered_net_buy_qty: int
    individual_net_buy_qty: int
    institution_net_buy_qty: int
    securities_net_buy_qty: int
    investment_trust_net_buy_qty: int
    private_equity_net_buy_qty: int
    bank_net_buy_qty: int
    insurance_net_buy_qty: int
    merchant_bank_net_buy_qty: int
    pension_fund_net_buy_qty: int
    other_corporation_net_buy_qty: int
    other_organization_net_buy_qty: int
    foreign_net_buy_amount: Decimal
    institution_net_buy_amount: Decimal
    individual_net_buy_amount: Decimal

    @classmethod
    def from_payload(cls, row: dict[str, Any]) -> "StockTradeDailyRow":
        raw_date = str(row.get("stck_bsop_date", "")).strip()
        if len(raw_date) != 8 or not raw_date.isdigit():
            raise KisPayloadError(f"KIS returned an unreadable stck_bsop_date: {raw_date!r}")
        business_date = date(int(raw_date[:4]), int(raw_date[4:6]), int(raw_date[6:]))

        values: dict[str, Any] = {
            "business_date": business_date,
            "open_price": _decimal(row.get("stck_oprc"), "stck_oprc"),
            "high_price": _decimal(row.get("stck_hgpr"), "stck_hgpr"),
            "low_price": _decimal(row.get("stck_lwpr"), "stck_lwpr"),
            "close_price": _decimal(row.get("stck_clpr"), "stck_clpr"),
            "accumulated_volume": _int(row.get("acml_vol"), "acml_vol"),
            # 이 칸만 원 단위다. 투자자별 대금은 백만원이다(실측).
            "accumulated_trade_amount": _decimal(row.get("acml_tr_pbmn"), "acml_tr_pbmn"),
            "foreign_registered_net_buy_qty": _int(row.get("frgn_reg_ntby_qty"), "frgn_reg_ntby_qty"),
            "foreign_unregistered_net_buy_qty": _int(row.get("frgn_nreg_ntby_qty"), "frgn_nreg_ntby_qty"),
        }
        for name, prefix in (("foreign", "frgn"), ("institution", "orgn"), ("individual", "prsn")):
            values[f"{name}_net_buy_qty"] = _int(row.get(f"{prefix}_ntby_qty"), f"{prefix}_ntby_qty")
            values[f"{name}_net_buy_amount"] = _decimal(row.get(f"{prefix}_ntby_tr_pbmn"), f"{prefix}_ntby_tr_pbmn")
        for name, field in INSTITUTION_PARTS + OTHER_PARTS:
            values[f"{name}_net_buy_qty"] = _int(row.get(field), field)

        # 일봉 네 값의 대소가 맞는지 본다. 필드를 잘못 짚으면 고가가 저가보다 낮아진다.
        # 값이 0인 행은 상장 전이나 거래정지라 검사에서 뺀다.
        candle = (values["open_price"], values["high_price"], values["low_price"], values["close_price"])
        if all(candle) and not (values["low_price"] <= min(candle) and values["high_price"] >= max(candle)):
            raise KisPayloadError(f"daily candle is inconsistent on {business_date}: {candle}")

        registered = values["foreign_registered_net_buy_qty"] + values["foreign_unregistered_net_buy_qty"]
        if registered != values["foreign_net_buy_qty"]:
            raise KisPayloadError(
                f"foreign parts do not add up on {business_date}: {registered} != {values['foreign_net_buy_qty']}"
            )

        parts = sum(values[f"{name}_net_buy_qty"] for name, _ in INSTITUTION_PARTS)
        if parts != values["institution_net_buy_qty"]:
            raise KisPayloadError(
                f"institution parts do not add up on {business_date}: {parts} != {values['institution_net_buy_qty']}"
            )

        # 기타 합계를 제공처가 따로 준다. 우리가 더한 값과 대조해 둘 중 하나가 다른 뜻으로
        # 바뀌는 것을 잡는다.
        others = values["other_corporation_net_buy_qty"] + values["other_organization_net_buy_qty"]
        reported_others = _int(row.get("etc_ntby_qty"), "etc_ntby_qty")
        if others != reported_others:
            raise KisPayloadError(f"other parts do not add up on {business_date}: {others} != {reported_others}")

        closed = (
            values["individual_net_buy_qty"]
            + values["foreign_net_buy_qty"]
            + values["institution_net_buy_qty"]
            + others
        )
        if closed != 0:
            # 시장 전체는 닫혀 있다. 누가 팔면 누군가는 받는다(실측: 정확히 0).
            raise KisPayloadError(f"investor categories do not close to zero on {business_date}: {closed}")

        return cls(**values)


class StockTradeDailyFetch(BaseModel):
    model_config = ConfigDict(frozen=True)

    stock_code: str
    end_date: date
    rows: tuple[StockTradeDailyRow, ...]
    started_at: datetime
    completed_at: datetime


def fetch_stock_trade_daily(
    token: SecretStr,
    app_key: SecretStr,
    app_secret: SecretStr,
    stock: InvestorFlowStock,
    end_date: date,
) -> StockTradeDailyFetch:
    """한 종목의 확정 일별 투자자 매매동향을 받는다.

    `end_date`는 **구간의 끝**이다. 한 응답이 그날부터 과거로 30 거래일을 담는다(실측:
    2026-07-01을 넣으면 2026-07-01~2026-05-19). 그래서 백필은 날짜를 뒤로 걸으면 된다.

    당일치는 장 마감 뒤에만 확정이다. 시각 판단은 DAG가 한다.
    """
    started_at = datetime.now(UTC)
    payload = _call(
        token,
        app_key,
        app_secret,
        DAILY_TRADE_PATH,
        DAILY_TRADE_TR_ID,
        {
            "FID_COND_MRKT_DIV_CODE": DAILY_TRADE_MARKET_DIV,
            "FID_INPUT_ISCD": stock.value,
            "FID_INPUT_DATE_1": end_date.strftime("%Y%m%d"),
            "FID_ORG_ADJ_PRC": "",
            "FID_ETC_CLS_CODE": "",
        },
    )
    raw = _rows(payload, "output2")
    try:
        rows = tuple(StockTradeDailyRow.from_payload(row) for row in raw)
    except (KeyError, ValidationError) as error:
        raise KisPayloadError(f"KIS daily trade row is malformed: {error}") from None

    dates = {row.business_date for row in rows}
    if len(dates) != len(rows):
        raise KisPayloadError(f"KIS returned duplicated business dates for {stock.value}")

    late = [row.business_date for row in rows if row.business_date > end_date]
    if late:
        # 구간 끝보다 뒤의 거래일이 섞이면 요청 날짜의 뜻이 바뀐 것이다. 백필이 조용히
        # 같은 구간을 맴돌게 되므로 멈춘다.
        raise KisPayloadError(f"KIS returned rows after {end_date}: {sorted(late)}")

    return StockTradeDailyFetch(
        stock_code=stock.value,
        end_date=end_date,
        rows=rows,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


def store_stock_trade_daily(connection: Connection, fetch: StockTradeDailyFetch) -> int:
    """거래일마다 한 행을 저장한다. 확정값이라 다시 받아도 같은 값으로 갱신된다."""
    rows = fetch.rows
    covered = sorted(row.business_date for row in rows)
    with connection.cursor() as cursor:
        source_record_id = _insert_source_record(
            cursor,
            DAILY_TRADE_SOURCE_KEY,
            fetch.started_at,
            fetch.completed_at,
            len(rows),
            {
                "stock_code": fetch.stock_code,
                "end_date": fetch.end_date.isoformat(),
                # 어느 구간이 이 응답에 들어 있었는지를 남긴다. 백필이 어디까지 갔는지
                # 계보만으로 읽을 수 있어야 한다.
                "covered_from": covered[0].isoformat() if covered else None,
                "covered_to": covered[-1].isoformat() if covered else None,
            },
        )
        execute_upserts(
            cursor,
            DAILY_TRADE_UPSERT,
            [
                (
                    fetch.stock_code,
                    row.business_date,
                    row.open_price,
                    row.high_price,
                    row.low_price,
                    row.close_price,
                    row.accumulated_volume,
                    row.accumulated_trade_amount,
                    row.foreign_net_buy_qty,
                    row.foreign_registered_net_buy_qty,
                    row.foreign_unregistered_net_buy_qty,
                    row.individual_net_buy_qty,
                    row.institution_net_buy_qty,
                    row.securities_net_buy_qty,
                    row.investment_trust_net_buy_qty,
                    row.private_equity_net_buy_qty,
                    row.bank_net_buy_qty,
                    row.insurance_net_buy_qty,
                    row.merchant_bank_net_buy_qty,
                    row.pension_fund_net_buy_qty,
                    row.other_corporation_net_buy_qty,
                    row.other_organization_net_buy_qty,
                    row.foreign_net_buy_amount,
                    row.institution_net_buy_amount,
                    row.individual_net_buy_amount,
                    source_record_id,
                )
                for row in rows
            ],
        )
    return len(rows)
