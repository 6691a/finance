"""한국투자증권 API에서 신용·증시자금·공매도·대차 일별 지표를 수집한다.

가격이 "얼마에 거래됐나"라면 이 값들은 **"누가 어떤 포지션으로 들고 있나"**다. 신용융자
잔고, 공매도 수량, 대차 잔고, 고객예탁금이 그것이다. 다섯 데이터 모두 체결 틱이 아니라
날짜별 집계라 WebSocket 없이 하루 한 번 REST로 받는다.

토큰 발급과 HTTP는 `kis.py`가 갖고 있어 그대로 쓴다. `kis.py`를 더 키우지 않고 모듈을
나눈 것은 `kis_market_calendar.py`와 같은 이유다. 분봉 수집과 이 일별 수집은 스케줄도
실패 처리도 달라서, 한 파일에 두면 어느 상수가 어느 수집의 것인지 읽기 어려워진다.

저장 대상은 `krx_*` 다섯 테이블이다. 정의의 원본은 백엔드의 `apps/models/market.py`이며
여기 SQL의 컬럼 이름은 `tests/collectors/test_kis_positioning.py`가 그 모델 metadata와
대조한다.

아래 계약은 2026-08-13에 운영 키로 확인했다.

## 다섯 API가 날짜를 다루는 방식이 제각각이다

| API | 날짜 입력 | 한 응답 | 날짜의 뜻 |
| --- | --- | --- | --- |
| 신용잔고 일별 | **결제일** 하나 | 30건 | 거래일과 결제일이 따로 온다 |
| 신용잔고 상위 | 없음 | 100건 | 응답이 기준일을 알려 준다 |
| 증시자금 | 종료일 하나 | **100영업일** | 요청일 전날부터 과거로 |
| 공매도 | 시작일·종료일 | 구간만큼(69건 확인) | 영업일 |
| 대차거래 | 시작일·종료일 | 구간만큼(69건 확인) | 영업일 |

- **신용잔고 일별의 입력은 결제일이다.** 실측에서 결제 시차가 2영업일이었다. 그래서 거래일
  구간을 받으려면 요청 끝을 `SETTLEMENT_PADDING_DAYS`만큼 뒤로 밀고, 돌아온 행 중
  `deal_date`가 원래 구간 안인 것만 저장한다.
- **증시자금은 되돌아볼 일수를 줄 필요가 없다.** 한 번 부르면 5개월치가 온다.
- **신용잔고 상위는 과거 조회가 없다.** 배포 전 과거는 채울 수 없고 운영 시작일부터 쌓인다.
  기준일은 응답의 `stnd_date2`이고 `stnd_date1`이 비교일이다. 초판 설계는 이 둘을 반대로
  적었다.

## 대차거래의 조회 분류

`MRKT_DIV_CLS_CODE`는 거래장 구분이 아니라 조회 분류다. **`3`이 종목이고 `1`은 시장
전체다.** 실측에서 같은 요청에 코드만 바꿨더니 `1`의 종가가 코스피 지수(6,579.04)이고
거래량이 시장 전체(360,651,200)였다. 공식 예제가 `1`을 쓰는 것은 시장을 조회한 것이며,
그대로 따라 하면 종목 코드를 보냈는데도 시장 숫자가 종목 행에 들어간다.
"""

import json
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from modules.collectors.kis import (
    SOURCE,
    KisPayloadError,
    result_error,
    send_get,
)
from modules.db import Connection
from modules.sql import read_sql
from modules.upsert import execute_upserts

logger = logging.getLogger(__name__)

CREDIT_BALANCE_PATH = "/uapi/domestic-stock/v1/quotations/daily-credit-balance"
CREDIT_BALANCE_TR_ID = "FHPST04760000"
CREDIT_BALANCE_SCREEN = "20476"
CREDIT_BALANCE_SOURCE_KEY = "daily_credit_balance"

CREDIT_RANKING_PATH = "/uapi/domestic-stock/v1/ranking/credit-balance"
CREDIT_RANKING_TR_ID = "FHKST17010000"
CREDIT_RANKING_SCREEN = "11701"
CREDIT_RANKING_SOURCE_KEY = "credit_balance_ranking"

MARKET_FUNDS_PATH = "/uapi/domestic-stock/v1/quotations/mktfunds"
MARKET_FUNDS_TR_ID = "FHKST649100C0"
MARKET_FUNDS_SOURCE_KEY = "market_funds"
MARKET_LENDING_SOURCE_KEY = "market_loan_trans"

SHORT_SALE_PATH = "/uapi/domestic-stock/v1/quotations/daily-short-sale"
SHORT_SALE_TR_ID = "FHPST04830000"
SHORT_SALE_SOURCE_KEY = "daily_short_sale"

LENDING_PATH = "/uapi/domestic-stock/v1/quotations/daily-loan-trans"
LENDING_TR_ID = "HHPST074500C0"
LENDING_SOURCE_KEY = "daily_loan_trans"

# 국내주식 상품 구분. **거래장 selector가 아니다.**
DOMESTIC_STOCK_DIVISION = "J"

# 대차거래 조회 분류. `3`이 종목이고 `1`·`2`가 시장 전체다(실측).
LENDING_STOCK_DIVISION = "3"

# 신용잔고 상위 조회 조건. 융자잔고금액 상위 한 종류만 받는다.
#
# **조회 대상은 둘이다.** `0000`이 전체이고 `1001`이 코스닥이다(실측: 전체 1위 삼성전자,
# 코스닥 1위 알테오젠). `0001`은 전체와 같은 응답을 준다. 응답 헤더의 `bstp_cls_code`는
# 요청값과 다른 체계라 읽지 않고, 저장하는 `universe_code`는 우리가 보낸 값이다.
RANKING_UNIVERSES: tuple[tuple[str, str], ...] = (("0000", "전체"), ("1001", "코스닥"))
RANKING_SORT_LOAN_BALANCE_AMOUNT = "2"
RANKING_PERIOD_DAYS = 5

# 신용잔고 일별의 입력이 결제일이라 거래일 구간보다 뒤를 더 받아야 한다. 결제 시차와 연휴를
# 거래소 캘린더 없이 흡수하는 운영 padding이며 공식 SLA가 아니다(실측 시차는 2영업일).
SETTLEMENT_PADDING_DAYS = 14

CREDIT_BALANCE_UPSERT = read_sql("postgres", "krx_stock_credit_balance_daily", "upsert.sql")
CREDIT_RANKING_UPSERT = read_sql("postgres", "krx_credit_balance_ranking_daily", "upsert.sql")
CREDIT_RANKING_DELETE_STALE = read_sql("postgres", "krx_credit_balance_ranking_daily", "delete_stale_ranks.sql")
MARKET_FUNDS_UPSERT = read_sql("postgres", "krx_market_funds_daily", "upsert.sql")
MARKET_LENDING_UPSERT = read_sql("postgres", "krx_market_securities_lending_daily", "upsert.sql")
SHORT_SALE_UPSERT = read_sql("postgres", "krx_stock_short_sale_daily", "upsert.sql")
LENDING_UPSERT = read_sql("postgres", "krx_stock_securities_lending_daily", "upsert.sql")
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")


class LendingMarket(StrEnum):
    """시장 전체 대차를 받을 시장. 값이 `market_movement_snapshot.symbol`과 같다.

    `division`은 같은 endpoint의 조회 분류다. 종목 조회(`3`)와 달리 종목 코드를 무시하고
    시장 전체를 돌려준다.

    **합계(`5`)는 두지 않는다.** 실측에서 5영업일 내내 두 시장의 정확한 합이었다. 유도되는
    값을 한 벌 더 저장하면 둘이 어긋날 때 어느 쪽이 맞는지 알 수 없다.
    """

    division: str

    def __new__(cls, market: str, division: str) -> Self:
        member = str.__new__(cls, market)
        member._value_ = market
        member.division = division
        return member

    KOSPI = ("KOSPI", "1")
    KOSDAQ = ("KOSDAQ", "2")


class PositioningStock(StrEnum):
    """수집 대상 종목. 값이 한국거래소 6자리 코드다.

    `disclosure_event.stock_code`와 같은 체계라 공시와 포지션을 한 키로 잇는다. 내부 이름
    (`SAMSUNG_ELECTRONICS` 같은 것)을 새로 만들지 않는 이유가 그것이다.

    `modules.collectors.document.dart.DartCompany`와 값이 같아야 하고
    `tests/collectors/test_kis_positioning.py`가 둘을 대조한다. 여기서 그 모듈을 import하지
    않는 것은 수집기끼리 엮이지 않게 하기 위해서다.
    """

    label: str

    def __new__(cls, code: str, label: str) -> Self:
        member = str.__new__(cls, code)
        member._value_ = code
        member.label = label
        return member

    SAMSUNG_ELECTRONICS = ("005930", "삼성전자")
    SK_HYNIX = ("000660", "SK하이닉스")


def _day(value: str, field: str) -> date:
    """`YYYYMMDD`. `strptime`은 naive datetime을 만들어 쓰지 않는다."""
    text = str(value).strip()
    if len(text) != 8 or not text.isdigit():
        raise KisPayloadError(f"{field} must be YYYYMMDD, got {value!r}")
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        raise KisPayloadError(f"{field} is not a real date: {value!r}") from None


def _decimal(value: Any, field: str) -> Decimal:
    """금액·비율 한 칸. 공백 패딩과 쉼표가 붙어 오고 음수는 정상값이다."""
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text or text == "-":
        # **여기서는 0이 진짜 값이다**(2026-08-28 판정) — 칸이 순매수 수량·금액·잔고라
        # "그 투자자가 그날 순매수 0"이 정상 관측이고 제공처도 그 뜻으로 빈 칸을 준다.
        # 목표주가처럼 0이 말이 안 되는 칸은 반대로 실패시킨다
        # (`collectors/analyst/kis_opinion.py`의 같은 이름 함수).
        return Decimal(0)
    try:
        return Decimal(text)
    except InvalidOperation:
        raise KisPayloadError(f"KIS returned a non-numeric {field}: {value!r}") from None


def _int(value: Any, field: str) -> int:
    """수량 한 칸. 소수점이 붙어 오는 필드가 있어 Decimal을 거쳐 자른다."""
    amount = _decimal(value, field)
    if amount != amount.to_integral_value():
        raise KisPayloadError(f"KIS returned a fractional {field}: {value!r}")
    return int(amount)


class CreditBalanceRow(BaseModel):
    """종목별 신용잔고 하루치."""

    model_config = ConfigDict(frozen=True)

    trade_date: date
    settlement_date: date
    close_price: Decimal
    accumulated_volume: int
    loan_new_quantity: int
    loan_repayment_quantity: int
    loan_balance_quantity: int
    loan_new_amount: Decimal
    loan_repayment_amount: Decimal
    loan_balance_amount: Decimal
    loan_balance_rate: Decimal
    loan_supply_rate: Decimal
    short_loan_new_quantity: int
    short_loan_repayment_quantity: int
    short_loan_balance_quantity: int
    short_loan_new_amount: Decimal
    short_loan_repayment_amount: Decimal
    short_loan_balance_amount: Decimal
    short_loan_balance_rate: Decimal
    short_loan_supply_rate: Decimal

    @classmethod
    def from_payload(cls, row: dict[str, Any]) -> "CreditBalanceRow":
        return cls(
            trade_date=_day(row["deal_date"], "deal_date"),
            settlement_date=_day(row["stlm_date"], "stlm_date"),
            close_price=_decimal(row.get("stck_prpr"), "stck_prpr"),
            accumulated_volume=_int(row.get("acml_vol"), "acml_vol"),
            loan_new_quantity=_int(row.get("whol_loan_new_stcn"), "whol_loan_new_stcn"),
            loan_repayment_quantity=_int(row.get("whol_loan_rdmp_stcn"), "whol_loan_rdmp_stcn"),
            loan_balance_quantity=_int(row.get("whol_loan_rmnd_stcn"), "whol_loan_rmnd_stcn"),
            loan_new_amount=_decimal(row.get("whol_loan_new_amt"), "whol_loan_new_amt"),
            loan_repayment_amount=_decimal(row.get("whol_loan_rdmp_amt"), "whol_loan_rdmp_amt"),
            loan_balance_amount=_decimal(row.get("whol_loan_rmnd_amt"), "whol_loan_rmnd_amt"),
            loan_balance_rate=_decimal(row.get("whol_loan_rmnd_rate"), "whol_loan_rmnd_rate"),
            loan_supply_rate=_decimal(row.get("whol_loan_gvrt"), "whol_loan_gvrt"),
            short_loan_new_quantity=_int(row.get("whol_stln_new_stcn"), "whol_stln_new_stcn"),
            short_loan_repayment_quantity=_int(row.get("whol_stln_rdmp_stcn"), "whol_stln_rdmp_stcn"),
            short_loan_balance_quantity=_int(row.get("whol_stln_rmnd_stcn"), "whol_stln_rmnd_stcn"),
            short_loan_new_amount=_decimal(row.get("whol_stln_new_amt"), "whol_stln_new_amt"),
            short_loan_repayment_amount=_decimal(row.get("whol_stln_rdmp_amt"), "whol_stln_rdmp_amt"),
            short_loan_balance_amount=_decimal(row.get("whol_stln_rmnd_amt"), "whol_stln_rmnd_amt"),
            short_loan_balance_rate=_decimal(row.get("whol_stln_rmnd_rate"), "whol_stln_rmnd_rate"),
            short_loan_supply_rate=_decimal(row.get("whol_stln_gvrt"), "whol_stln_gvrt"),
        )


class RankingRow(BaseModel):
    """신용잔고 상위 한 칸."""

    model_config = ConfigDict(frozen=True)

    rank: int
    stock_code: str
    stock_name: str
    close_price: Decimal
    accumulated_volume: int
    loan_balance_quantity: int
    loan_balance_amount: Decimal
    loan_balance_rate: Decimal
    short_loan_balance_quantity: int
    short_loan_balance_amount: Decimal
    short_loan_balance_rate: Decimal
    loan_balance_growth_rate: Decimal
    short_loan_balance_growth_rate: Decimal

    @classmethod
    def from_payload(cls, rank: int, row: dict[str, Any]) -> "RankingRow":
        code = str(row["mksc_shrn_iscd"]).strip()
        # **숫자만은 아니다.** 실측에서 `0126Z0` 같은 코드가 왔다. 신주인수권증서·ETN 등은
        # 영문자가 섞인 단축코드를 쓴다. 여섯 자리 영숫자만 확인한다.
        if len(code) != 6 or not code.isalnum():
            raise KisPayloadError(f"ranking row has a malformed stock code: {code!r}")
        return cls(
            rank=rank,
            stock_code=code,
            stock_name=str(row.get("hts_kor_isnm", "")).strip(),
            close_price=_decimal(row.get("stck_prpr"), "stck_prpr"),
            accumulated_volume=_int(row.get("acml_vol"), "acml_vol"),
            loan_balance_quantity=_int(row.get("whol_loan_rmnd_stcn"), "whol_loan_rmnd_stcn"),
            loan_balance_amount=_decimal(row.get("whol_loan_rmnd_amt"), "whol_loan_rmnd_amt"),
            loan_balance_rate=_decimal(row.get("whol_loan_rmnd_rate"), "whol_loan_rmnd_rate"),
            short_loan_balance_quantity=_int(row.get("whol_stln_rmnd_stcn"), "whol_stln_rmnd_stcn"),
            short_loan_balance_amount=_decimal(row.get("whol_stln_rmnd_amt"), "whol_stln_rmnd_amt"),
            short_loan_balance_rate=_decimal(row.get("whol_stln_rmnd_rate"), "whol_stln_rmnd_rate"),
            loan_balance_growth_rate=_decimal(row.get("nday_vrss_loan_rmnd_inrt"), "nday_vrss_loan_rmnd_inrt"),
            short_loan_balance_growth_rate=_decimal(row.get("nday_vrss_stln_rmnd_inrt"), "nday_vrss_stln_rmnd_inrt"),
        )


class MarketFundsRow(BaseModel):
    """증시자금 하루치."""

    model_config = ConfigDict(frozen=True)

    business_date: date
    index_close: Decimal
    index_change: Decimal
    market_capitalization: Decimal
    customer_deposit: Decimal
    customer_deposit_change: Decimal
    turnover_ratio: Decimal
    unsettled_amount: Decimal
    credit_loan_balance: Decimal
    futures_margin_amount: Decimal
    equity_fund_amount: Decimal
    mixed_fund_amount: Decimal
    bond_fund_amount: Decimal
    mmf_amount: Decimal
    securities_lending_amount: Decimal

    @classmethod
    def from_payload(cls, row: dict[str, Any]) -> "MarketFundsRow":
        # `prdy_ctrt`는 읽지 않는다. 실측 값이 등락률과 맞지 않았다.
        return cls(
            business_date=_day(row["bsop_date"], "bsop_date"),
            index_close=_decimal(row.get("bstp_nmix_prpr"), "bstp_nmix_prpr"),
            index_change=_decimal(row.get("bstp_nmix_prdy_vrss"), "bstp_nmix_prdy_vrss"),
            market_capitalization=_decimal(row.get("hts_avls"), "hts_avls"),
            customer_deposit=_decimal(row.get("cust_dpmn_amt"), "cust_dpmn_amt"),
            customer_deposit_change=_decimal(row.get("cust_dpmn_amt_prdy_vrss"), "cust_dpmn_amt_prdy_vrss"),
            turnover_ratio=_decimal(row.get("amt_tnrt"), "amt_tnrt"),
            unsettled_amount=_decimal(row.get("uncl_amt"), "uncl_amt"),
            credit_loan_balance=_decimal(row.get("crdt_loan_rmnd"), "crdt_loan_rmnd"),
            futures_margin_amount=_decimal(row.get("futs_tfam_amt"), "futs_tfam_amt"),
            equity_fund_amount=_decimal(row.get("sttp_amt"), "sttp_amt"),
            mixed_fund_amount=_decimal(row.get("mxtp_amt"), "mxtp_amt"),
            bond_fund_amount=_decimal(row.get("bntp_amt"), "bntp_amt"),
            mmf_amount=_decimal(row.get("mmf_amt"), "mmf_amt"),
            securities_lending_amount=_decimal(row.get("secu_lend_amt"), "secu_lend_amt"),
        )


class ShortSaleRow(BaseModel):
    """종목별 공매도 하루치."""

    model_config = ConfigDict(frozen=True)

    business_date: date
    close_price: Decimal
    accumulated_volume: int
    short_sale_quantity: int
    short_sale_volume_ratio: Decimal
    accumulated_short_sale_quantity: int
    accumulated_short_sale_volume_ratio: Decimal
    short_sale_amount: Decimal
    short_sale_amount_ratio: Decimal
    accumulated_short_sale_amount: Decimal
    accumulated_short_sale_amount_ratio: Decimal
    total_amount: Decimal
    short_sale_average_price: Decimal

    @classmethod
    def from_payload(cls, row: dict[str, Any]) -> "ShortSaleRow":
        return cls(
            business_date=_day(row["stck_bsop_date"], "stck_bsop_date"),
            close_price=_decimal(row.get("stck_clpr"), "stck_clpr"),
            accumulated_volume=_int(row.get("acml_vol"), "acml_vol"),
            short_sale_quantity=_int(row.get("ssts_cntg_qty"), "ssts_cntg_qty"),
            short_sale_volume_ratio=_decimal(row.get("ssts_vol_rlim"), "ssts_vol_rlim"),
            accumulated_short_sale_quantity=_int(row.get("acml_ssts_cntg_qty"), "acml_ssts_cntg_qty"),
            accumulated_short_sale_volume_ratio=_decimal(row.get("acml_ssts_cntg_qty_rlim"), "acml_ssts_cntg_qty_rlim"),
            short_sale_amount=_decimal(row.get("ssts_tr_pbmn"), "ssts_tr_pbmn"),
            short_sale_amount_ratio=_decimal(row.get("ssts_tr_pbmn_rlim"), "ssts_tr_pbmn_rlim"),
            accumulated_short_sale_amount=_decimal(row.get("acml_ssts_tr_pbmn"), "acml_ssts_tr_pbmn"),
            accumulated_short_sale_amount_ratio=_decimal(row.get("acml_ssts_tr_pbmn_rlim"), "acml_ssts_tr_pbmn_rlim"),
            total_amount=_decimal(row.get("acml_tr_pbmn"), "acml_tr_pbmn"),
            short_sale_average_price=_decimal(row.get("avrg_prc"), "avrg_prc"),
        )


class LendingRow(BaseModel):
    """종목별 대차거래 하루치."""

    model_config = ConfigDict(frozen=True)

    business_date: date
    close_price: Decimal
    price_change: Decimal
    accumulated_volume: int
    new_quantity: int
    repayment_quantity: int
    balance_change_quantity: int
    balance_quantity: int
    balance_amount: Decimal

    @classmethod
    def from_payload(cls, row: dict[str, Any]) -> "LendingRow":
        return cls(
            business_date=_day(row["bsop_date"], "bsop_date"),
            close_price=_decimal(row.get("stck_prpr"), "stck_prpr"),
            price_change=_decimal(row.get("prdy_vrss"), "prdy_vrss"),
            accumulated_volume=_int(row.get("acml_vol"), "acml_vol"),
            new_quantity=_int(row.get("new_stcn"), "new_stcn"),
            repayment_quantity=_int(row.get("rdmp_stcn"), "rdmp_stcn"),
            balance_change_quantity=_int(row.get("prdy_rmnd_vrss"), "prdy_rmnd_vrss"),
            balance_quantity=_int(row.get("rmnd_stcn"), "rmnd_stcn"),
            balance_amount=_decimal(row.get("rmnd_amt"), "rmnd_amt"),
        )


class Fetch(BaseModel):
    """조회 한 번. 저장에 쓸 행과 계보에 남길 값을 함께 담는다."""

    model_config = ConfigDict(frozen=True)

    source_key: str
    stock_code: str | None
    market_code: str | None = None
    rows: tuple[Any, ...]
    metadata: dict[str, Any]
    started_at: datetime
    completed_at: datetime


def _rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    output = payload.get(key) or []
    if isinstance(output, dict):
        return [output]
    if not isinstance(output, list):
        raise KisPayloadError(f"KIS returned a {key} that is neither a list nor an object")
    return output


def _reject_future_rows(rows: Sequence[Any], observation_end: date, label: str) -> None:
    """요청 종료일보다 뒤인 행은 받지 않는다. 구간을 잘못 보냈다는 뜻이다."""
    future = [row.business_date for row in rows if row.business_date > observation_end]
    if future:
        raise KisPayloadError(f"KIS {label} returned rows after {observation_end}: {sorted(future)[:3]}")


def _insert_source_record(cursor: Any, fetch: Fetch, stored: int) -> int:
    cursor.execute(
        SOURCE_RECORD_INSERT,
        (
            "api",
            SOURCE,
            fetch.source_key,
            fetch.started_at,
            fetch.completed_at,
            "succeeded",
            stored,
            # 원본은 남기지 않는다. 매일 도는 조회라 계보가 데이터보다 빨리 커진다.
            None,
            json.dumps(fetch.metadata, ensure_ascii=False),
        ),
    )
    return cursor.fetchone()[0]


class KisPositioningCollector:
    """KIS 포지션 지표 수집기. 자격 증명과 토큰을 들고 신용·공매도·대차·증시자금을 조회·저장한다.

    한 실행이 객체 하나다. 토큰은 발급 횟수 제한이 있어 DAG이 한 번 받아 넘긴다. 토큰은 이
    객체가 사는 동안 안 변하는 값이라 갈아 끼우지 않는다 — 401로 다시 받았으면 DAG이 새
    토큰으로 객체를 다시 만든다.

    행 파싱(`*Row.from_payload`)과 `_rows`·`_reject_future_rows`·`_insert_source_record`는
    밖에 둔다. 자격 증명을 보지 않는 변환이거나 커서만 받는 저장 조각이다.
    """

    def __init__(self, token: SecretStr, app_key: SecretStr, app_secret: SecretStr) -> None:
        self._token = token
        self._app_key = app_key
        self._app_secret = app_secret

    def _call(
        self,
        path: str,
        tr_id: str,
        query: dict[str, str],
    ) -> dict[str, Any]:
        body, _, _ = send_get(self._token, self._app_key, self._app_secret, path, tr_id, query)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise KisPayloadError(f"KIS returned a non-JSON body: {error}") from None
        if not isinstance(payload, dict):
            raise KisPayloadError("KIS returned a JSON body that is not an object")

        code = str(payload.get("rt_cd", ""))
        if code != "0":
            raise result_error(code, str(payload.get("msg1", "")).strip())
        return payload

    def fetch_credit_balance(
        self,
        stock: PositioningStock,
        observation_start: date,
        observation_end: date,
    ) -> Fetch:
        """종목별 신용잔고를 받는다. **요청은 결제일, 저장은 거래일 기준이다.**

        입력이 결제일이라 거래일 구간의 끝보다 뒤를 요청해야 그 거래일의 행이 들어온다. 돌아온
        행 중 `deal_date`가 원래 구간 안인 것만 남긴다.
        """
        started_at = datetime.now(UTC)
        requested = min(observation_end + timedelta(days=SETTLEMENT_PADDING_DAYS), datetime.now(UTC).date())
        payload = self._call(
            CREDIT_BALANCE_PATH,
            CREDIT_BALANCE_TR_ID,
            {
                "FID_COND_MRKT_DIV_CODE": DOMESTIC_STOCK_DIVISION,
                "FID_COND_SCR_DIV_CODE": CREDIT_BALANCE_SCREEN,
                "FID_INPUT_ISCD": stock.value,
                "FID_INPUT_DATE_1": requested.strftime("%Y%m%d"),
            },
        )
        raw = _rows(payload, "output")
        try:
            parsed = [CreditBalanceRow.from_payload(row) for row in raw]
        except (KeyError, ValidationError) as error:
            raise KisPayloadError(f"KIS credit balance row is malformed: {error}") from None

        kept = tuple(row for row in parsed if observation_start <= row.trade_date <= observation_end)
        return Fetch(
            source_key=CREDIT_BALANCE_SOURCE_KEY,
            stock_code=stock.value,
            rows=kept,
            metadata={
                "stock_code": stock.value,
                "requested_settlement_date": requested.isoformat(),
                "observation_start": observation_start.isoformat(),
                "observation_end": observation_end.isoformat(),
                "returned": len(raw),
                "kept": len(kept),
            },
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def fetch_credit_ranking(
        self,
        universe: str = "0000",
    ) -> Fetch:
        """융자잔고금액 상위 스냅샷을 받는다.

        과거 기준일 입력이 없어 늘 최신 한 벌이다. 기준일은 응답이 알려 준다.

        `universe`는 조회 대상이다. `0000`이 전체, `1001`이 코스닥이다(실측).
        """
        started_at = datetime.now(UTC)
        payload = self._call(
            CREDIT_RANKING_PATH,
            CREDIT_RANKING_TR_ID,
            {
                "FID_COND_SCR_DIV_CODE": CREDIT_RANKING_SCREEN,
                "FID_COND_MRKT_DIV_CODE": DOMESTIC_STOCK_DIVISION,
                "FID_INPUT_ISCD": universe,
                "FID_OPTION": str(RANKING_PERIOD_DAYS),
                "FID_RANK_SORT_CLS_CODE": RANKING_SORT_LOAN_BALANCE_AMOUNT,
            },
        )

        head = _rows(payload, "output1")
        if not head:
            raise KisPayloadError("credit ranking response has no output1 with the standard dates")
        # **stnd_date2가 기준일이고 stnd_date1이 비교일이다.** 초판 문서가 반대로 적었다.
        standard_date = _day(head[0]["stnd_date2"], "stnd_date2")
        comparison_date = _day(head[0]["stnd_date1"], "stnd_date1")
        if comparison_date >= standard_date:
            raise KisPayloadError(f"credit ranking dates are not ordered: {comparison_date} >= {standard_date}")

        raw = _rows(payload, "output2")
        if not raw:
            # 이 API는 늘 최신 완전 스냅샷을 준다. 빈 배열은 휴장이 아니라 고장이다.
            raise KisPayloadError("credit ranking returned no rows")

        try:
            rows = tuple(RankingRow.from_payload(rank, row) for rank, row in enumerate(raw, start=1))
        except (KeyError, ValidationError) as error:
            raise KisPayloadError(f"KIS credit ranking row is malformed: {error}") from None

        codes = {row.stock_code for row in rows}
        if len(codes) != len(rows):
            raise KisPayloadError("credit ranking returned duplicated stock codes")

        return Fetch(
            source_key=CREDIT_RANKING_SOURCE_KEY,
            stock_code=None,
            rows=rows,
            metadata={
                "standard_date": standard_date.isoformat(),
                "comparison_date": comparison_date.isoformat(),
                "universe_code": universe,
                "sort_code": RANKING_SORT_LOAN_BALANCE_AMOUNT,
                "period_days": RANKING_PERIOD_DAYS,
                # 건수를 상수로 박지 않는다. 실측이 100이었을 뿐이다.
                "returned": len(rows),
            },
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def fetch_market_funds(
        self,
        observation_end: date,
    ) -> Fetch:
        """증시자금 종합을 받는다. **한 번 호출에 100영업일이 온다.**

        요청 날짜는 종료일이고 그 전날부터 과거로 채워진다. 그래서 되돌아볼 일수를 받지 않는다.
        """
        started_at = datetime.now(UTC)
        payload = self._call(
            MARKET_FUNDS_PATH,
            MARKET_FUNDS_TR_ID,
            {"FID_INPUT_DATE_1": observation_end.strftime("%Y%m%d")},
        )
        raw = _rows(payload, "output")
        try:
            rows = tuple(MarketFundsRow.from_payload(row) for row in raw)
        except (KeyError, ValidationError) as error:
            raise KisPayloadError(f"KIS market funds row is malformed: {error}") from None

        return Fetch(
            source_key=MARKET_FUNDS_SOURCE_KEY,
            stock_code=None,
            rows=rows,
            metadata={
                "requested_end_date": observation_end.isoformat(),
                "returned": len(rows),
                "first_date": rows[0].business_date.isoformat() if rows else None,
                "last_date": rows[-1].business_date.isoformat() if rows else None,
            },
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def fetch_short_sale(
        self,
        stock: PositioningStock,
        observation_start: date,
        observation_end: date,
    ) -> Fetch:
        """종목별 공매도를 받는다. 시세는 `output1`에 오지만 저장하지 않는다."""
        started_at = datetime.now(UTC)
        payload = self._call(
            SHORT_SALE_PATH,
            SHORT_SALE_TR_ID,
            {
                "FID_COND_MRKT_DIV_CODE": DOMESTIC_STOCK_DIVISION,
                "FID_INPUT_ISCD": stock.value,
                "FID_INPUT_DATE_1": observation_start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": observation_end.strftime("%Y%m%d"),
            },
        )
        raw = _rows(payload, "output2")
        try:
            rows = tuple(ShortSaleRow.from_payload(row) for row in raw)
        except (KeyError, ValidationError) as error:
            raise KisPayloadError(f"KIS short sale row is malformed: {error}") from None

        _reject_future_rows(rows, observation_end, "short sale")
        return Fetch(
            source_key=SHORT_SALE_SOURCE_KEY,
            stock_code=stock.value,
            rows=rows,
            metadata={
                "stock_code": stock.value,
                "observation_start": observation_start.isoformat(),
                "observation_end": observation_end.isoformat(),
                "returned": len(rows),
            },
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def fetch_lending(
        self,
        stock: PositioningStock,
        observation_start: date,
        observation_end: date,
    ) -> Fetch:
        """종목별 대차거래를 받는다.

        **`MRKT_DIV_CLS_CODE`는 반드시 종목 조회 값이다.** 시장 전체 값을 쓰면 종목 코드를
        보냈는데도 코스피 전체 숫자가 돌아온다(실측).
        """
        started_at = datetime.now(UTC)
        payload = self._call(
            LENDING_PATH,
            LENDING_TR_ID,
            {
                "MRKT_DIV_CLS_CODE": LENDING_STOCK_DIVISION,
                "MKSC_SHRN_ISCD": stock.value,
                "START_DATE": observation_start.strftime("%Y%m%d"),
                "END_DATE": observation_end.strftime("%Y%m%d"),
                "CTS": "",
            },
        )
        raw = _rows(payload, "output1")
        try:
            rows = tuple(LendingRow.from_payload(row) for row in raw)
        except (KeyError, ValidationError) as error:
            raise KisPayloadError(f"KIS lending row is malformed: {error}") from None

        _reject_future_rows(rows, observation_end, "lending")
        return Fetch(
            source_key=LENDING_SOURCE_KEY,
            stock_code=stock.value,
            rows=rows,
            metadata={
                "stock_code": stock.value,
                "observation_start": observation_start.isoformat(),
                "observation_end": observation_end.isoformat(),
                "returned": len(rows),
            },
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def fetch_market_lending(
        self,
        market: LendingMarket,
        observation_start: date,
        observation_end: date,
    ) -> Fetch:
        """시장 전체 대차거래를 받는다.

        같은 endpoint에서 조회 분류만 바꾼다. 종목 코드는 무시되지만 필수 파라미터라 아무 값이나
        넣지 않고 대표 종목을 그대로 보낸다. 응답의 `stck_prpr`은 주가가 아니라 **지수**다.
        """
        started_at = datetime.now(UTC)
        payload = self._call(
            LENDING_PATH,
            LENDING_TR_ID,
            {
                "MRKT_DIV_CLS_CODE": market.division,
                "MKSC_SHRN_ISCD": PositioningStock.SAMSUNG_ELECTRONICS.value,
                "START_DATE": observation_start.strftime("%Y%m%d"),
                "END_DATE": observation_end.strftime("%Y%m%d"),
                "CTS": "",
            },
        )
        raw = _rows(payload, "output1")
        try:
            rows = tuple(LendingRow.from_payload(row) for row in raw)
        except (KeyError, ValidationError) as error:
            raise KisPayloadError(f"KIS market lending row is malformed: {error}") from None

        _reject_future_rows(rows, observation_end, "market lending")
        return Fetch(
            source_key=MARKET_LENDING_SOURCE_KEY,
            stock_code=None,
            market_code=market.value,
            rows=rows,
            metadata={
                "market_code": market.value,
                "division": market.division,
                "observation_start": observation_start.isoformat(),
                "observation_end": observation_end.isoformat(),
                "returned": len(rows),
            },
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def store_credit_balance(self, connection: Connection, fetch: Fetch) -> int:
        with connection.cursor() as cursor:
            source_record_id = _insert_source_record(cursor, fetch, len(fetch.rows))
            execute_upserts(
                cursor,
                CREDIT_BALANCE_UPSERT,
                [
                    (
                        fetch.stock_code,
                        row.trade_date,
                        row.settlement_date,
                        row.close_price,
                        row.accumulated_volume,
                        row.loan_new_quantity,
                        row.loan_repayment_quantity,
                        row.loan_balance_quantity,
                        row.loan_new_amount,
                        row.loan_repayment_amount,
                        row.loan_balance_amount,
                        row.loan_balance_rate,
                        row.loan_supply_rate,
                        row.short_loan_new_quantity,
                        row.short_loan_repayment_quantity,
                        row.short_loan_balance_quantity,
                        row.short_loan_new_amount,
                        row.short_loan_repayment_amount,
                        row.short_loan_balance_amount,
                        row.short_loan_balance_rate,
                        row.short_loan_supply_rate,
                        source_record_id,
                    )
                    for row in fetch.rows
                ],
            )
        return len(fetch.rows)

    def store_credit_ranking(self, connection: Connection, fetch: Fetch) -> int:
        """순위 스냅샷을 저장하고 이번에 받은 마지막 순위 밖의 슬롯을 지운다."""
        standard_date = _day(fetch.metadata["standard_date"].replace("-", ""), "standard_date")
        comparison_date = _day(fetch.metadata["comparison_date"].replace("-", ""), "comparison_date")
        universe = fetch.metadata["universe_code"]
        sort_code = fetch.metadata["sort_code"]
        period_days = fetch.metadata["period_days"]

        with connection.cursor() as cursor:
            source_record_id = _insert_source_record(cursor, fetch, len(fetch.rows))
            execute_upserts(
                cursor,
                CREDIT_RANKING_UPSERT,
                [
                    (
                        standard_date,
                        comparison_date,
                        universe,
                        sort_code,
                        period_days,
                        row.rank,
                        row.stock_code,
                        row.stock_name,
                        row.close_price,
                        row.accumulated_volume,
                        row.loan_balance_quantity,
                        row.loan_balance_amount,
                        row.loan_balance_rate,
                        row.short_loan_balance_quantity,
                        row.short_loan_balance_amount,
                        row.short_loan_balance_rate,
                        row.loan_balance_growth_rate,
                        row.short_loan_balance_growth_rate,
                        source_record_id,
                    )
                    for row in fetch.rows
                ],
            )
            # 응답이 짧아졌으면 남는 슬롯을 같은 트랜잭션에서 지운다.
            cursor.execute(
                CREDIT_RANKING_DELETE_STALE,
                (standard_date, universe, sort_code, period_days, len(fetch.rows)),
            )
        return len(fetch.rows)

    def store_market_funds(self, connection: Connection, fetch: Fetch) -> int:
        with connection.cursor() as cursor:
            source_record_id = _insert_source_record(cursor, fetch, len(fetch.rows))
            execute_upserts(
                cursor,
                MARKET_FUNDS_UPSERT,
                [
                    (
                        row.business_date,
                        row.index_close,
                        row.index_change,
                        row.market_capitalization,
                        row.customer_deposit,
                        row.customer_deposit_change,
                        row.turnover_ratio,
                        row.unsettled_amount,
                        row.credit_loan_balance,
                        row.futures_margin_amount,
                        row.equity_fund_amount,
                        row.mixed_fund_amount,
                        row.bond_fund_amount,
                        row.mmf_amount,
                        row.securities_lending_amount,
                        source_record_id,
                    )
                    for row in fetch.rows
                ],
            )
        return len(fetch.rows)

    def store_short_sale(self, connection: Connection, fetch: Fetch) -> int:
        with connection.cursor() as cursor:
            source_record_id = _insert_source_record(cursor, fetch, len(fetch.rows))
            execute_upserts(
                cursor,
                SHORT_SALE_UPSERT,
                [
                    (
                        fetch.stock_code,
                        row.business_date,
                        row.close_price,
                        row.accumulated_volume,
                        row.short_sale_quantity,
                        row.short_sale_volume_ratio,
                        row.accumulated_short_sale_quantity,
                        row.accumulated_short_sale_volume_ratio,
                        row.short_sale_amount,
                        row.short_sale_amount_ratio,
                        row.accumulated_short_sale_amount,
                        row.accumulated_short_sale_amount_ratio,
                        row.total_amount,
                        row.short_sale_average_price,
                        source_record_id,
                    )
                    for row in fetch.rows
                ],
            )
        return len(fetch.rows)

    def store_market_lending(self, connection: Connection, fetch: Fetch) -> int:
        """시장 전체 대차를 저장한다. 종목 대차와 같은 행 모양이지만 종가 자리가 지수다."""
        with connection.cursor() as cursor:
            source_record_id = _insert_source_record(cursor, fetch, len(fetch.rows))
            execute_upserts(
                cursor,
                MARKET_LENDING_UPSERT,
                [
                    (
                        fetch.market_code,
                        row.business_date,
                        row.close_price,
                        row.price_change,
                        row.accumulated_volume,
                        row.new_quantity,
                        row.repayment_quantity,
                        row.balance_change_quantity,
                        row.balance_quantity,
                        row.balance_amount,
                        source_record_id,
                    )
                    for row in fetch.rows
                ],
            )
        return len(fetch.rows)

    def store_lending(self, connection: Connection, fetch: Fetch) -> int:
        with connection.cursor() as cursor:
            source_record_id = _insert_source_record(cursor, fetch, len(fetch.rows))
            execute_upserts(
                cursor,
                LENDING_UPSERT,
                [
                    (
                        fetch.stock_code,
                        row.business_date,
                        row.close_price,
                        row.price_change,
                        row.accumulated_volume,
                        row.new_quantity,
                        row.repayment_quantity,
                        row.balance_change_quantity,
                        row.balance_quantity,
                        row.balance_amount,
                        source_record_id,
                    )
                    for row in fetch.rows
                ],
            )
        return len(fetch.rows)
