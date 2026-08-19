"""KIS WebSocket 프레임 계약.

TR ID별 46필드 표와 파이프·캐럿 프레임 파싱, 그리고 계약 위반 오류를 정의한다.
필드 순서가 계약이다 — 열이 밀리면 값이 조용히 옆 칸으로 가므로 개수와 순서를
응답마다 검증한다(문서 3.5).
"""

import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator

from apps.models.market import StockExchange

KST = ZoneInfo("Asia/Seoul")

KRX_TR_ID = "H0STCNT0"
NXT_TR_ID = "H0NXCNT0"
RECORD_FIELD_COUNT = 46


class FrameContractError(ValueError):
    """프레임이 46필드 파이프·캐럿 계약을 지키지 않았다. 재시도해도 같은 답이다."""


class EncryptedFrameError(RuntimeError):
    """암호화(encrypt=Y) 프레임. 평문 파싱은 금지고 격리 후 재연결한다(문서 3.5)."""


# H0STCNT0(KRX 주식 체결) 46필드. 순서가 계약이다 — 열이 밀리면 값이 조용히 옆 칸으로
# 간다. 개수와 순서를 응답마다 검증한다. 실 캡처 픽스처로 대조한다.
KRX_FIELDS: tuple[str, ...] = (
    "MKSC_SHRN_ISCD",
    "STCK_CNTG_HOUR",
    "STCK_PRPR",
    "PRDY_VRSS_SIGN",
    "PRDY_VRSS",
    "PRDY_CTRT",
    "WGHN_AVRG_STCK_PRC",
    "STCK_OPRC",
    "STCK_HGPR",
    "STCK_LWPR",
    "ASKP1",
    "BIDP1",
    "CNTG_VOL",
    "ACML_VOL",
    "ACML_TR_PBMN",
    "SELN_CNTG_CSNU",
    "SHNU_CNTG_CSNU",
    "NTBY_CNTG_CSNU",
    "CTTR",
    "SELN_CNTG_SMTN",
    "SHNU_CNTG_SMTN",
    "CCLD_DVSN",
    "SHNU_RATE",
    "PRDY_VOL_VRSS_ACML_VOL_RATE",
    "OPRC_HOUR",
    "OPRC_VRSS_PRPR_SIGN",
    "OPRC_VRSS_PRPR",
    "HGPR_HOUR",
    "HGPR_VRSS_PRPR_SIGN",
    "HGPR_VRSS_PRPR",
    "LWPR_HOUR",
    "LWPR_VRSS_PRPR_SIGN",
    "LWPR_VRSS_PRPR",
    "BSOP_DATE",
    "NEW_MKOP_CLS_CODE",
    "TRHT_YN",
    "ASKP_RSQN1",
    "BIDP_RSQN1",
    "TOTAL_ASKP_RSQN",
    "TOTAL_BIDP_RSQN",
    "VOL_TNRT",
    "PRDY_SMNS_HOUR_ACML_VOL",
    "PRDY_SMNS_HOUR_ACML_VOL_RATE",
    "HOUR_CLS_CODE",
    "MRKT_TRTM_CLS_CODE",
    "VI_STND_PRC",
)

# H0NXCNT0(NXT 주식 체결)은 KRX와 필드 수가 같지만 스키마가 하나 다르다.
# KRX의 CCLD_DVSN 자리에 NXT는 CNTG_CLS_CODE가 온다(문서 3.5).
NXT_FIELDS: tuple[str, ...] = tuple("CNTG_CLS_CODE" if name == "CCLD_DVSN" else name for name in KRX_FIELDS)


class FrameSpec(BaseModel):
    """TR ID 하나의 프레임 계약. 필드 목록을 고정해 열 밀림을 잡는다."""

    model_config = ConfigDict(frozen=True)

    exchange: StockExchange
    fields: tuple[str, ...]

    @property
    def field_count(self) -> int:
        return len(self.fields)

    def index(self, name: str) -> int:
        return self.fields.index(name)


FRAME_SPECS: dict[str, FrameSpec] = {
    KRX_TR_ID: FrameSpec(exchange=StockExchange.KRX, fields=KRX_FIELDS),
    NXT_TR_ID: FrameSpec(exchange=StockExchange.NXT, fields=NXT_FIELDS),
}


class Tick(BaseModel):
    """체결 하나에서 집계에 쓰는 다섯 값만 추린 것."""

    model_config = ConfigDict(frozen=True)

    exchange: StockExchange
    stock_code: str
    occurred_at: AwareDatetime
    price: Decimal
    volume: int

    @field_validator("occurred_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @field_validator("price")
    @classmethod
    def require_positive_finite(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError(f"price must be positive and finite, got {value}")
        return value

    @field_validator("volume")
    @classmethod
    def require_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError(f"volume must not be negative, got {value}")
        return value


class PingPong(BaseModel):
    model_config = ConfigDict(frozen=True)

    raw: str


class SubscribeResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tr_id: str
    tr_key: str
    ok: bool
    code: str
    message: str


def parse_data_frame(raw: str, subscribed_codes: frozenset[str]) -> tuple[Tick, ...]:
    """`암호화|TR_ID|건수|필드^...` 데이터 프레임을 틱으로 바꾼다.

    검증에 실패하면 `FrameContractError`다. 열이 밀린 채 조용히 저장되는 것보다
    멈추는 편이 낫다.
    """
    head = raw.split("|", 3)
    if len(head) != 4:
        raise FrameContractError(f"data frame needs 4 pipe sections, got {len(head)}")
    encrypted, tr_id, count_raw, body = head

    if encrypted == "1":
        raise EncryptedFrameError(f"encrypted frame for {tr_id}")
    if encrypted != "0":
        raise FrameContractError(f"unknown encrypt flag {encrypted!r}")

    spec = FRAME_SPECS.get(tr_id)
    if spec is None:
        raise FrameContractError(f"unsupported TR ID {tr_id!r}")

    try:
        count = int(count_raw)
    except ValueError:
        raise FrameContractError(f"record count is not a number: {count_raw!r}") from None
    if count < 1:
        raise FrameContractError(f"record count must be >= 1, got {count}")

    fields = body.split("^")
    if len(fields) != count * spec.field_count:
        raise FrameContractError(
            f"{tr_id} expects {count * spec.field_count} fields for {count} records, got {len(fields)}"
        )

    code_index = spec.index("MKSC_SHRN_ISCD")
    date_index = spec.index("BSOP_DATE")
    hour_index = spec.index("STCK_CNTG_HOUR")
    price_index = spec.index("STCK_PRPR")
    volume_index = spec.index("CNTG_VOL")

    ticks = []
    for start in range(0, len(fields), spec.field_count):
        record = fields[start : start + spec.field_count]
        stock_code = record[code_index]
        if stock_code not in subscribed_codes:
            raise FrameContractError(f"unsubscribed stock code {stock_code!r} in {tr_id}")
        try:
            occurred_at = datetime.strptime(record[date_index] + record[hour_index], "%Y%m%d%H%M%S").replace(
                tzinfo=KST
            )
            price = Decimal(record[price_index])
            volume = int(record[volume_index])
        except (ValueError, InvalidOperation):
            raise FrameContractError(
                f"invalid tick fields for {stock_code}: date={record[date_index]!r} hour={record[hour_index]!r}"
            ) from None
        ticks.append(
            Tick(
                exchange=spec.exchange,
                stock_code=stock_code,
                occurred_at=occurred_at,
                price=price,
                volume=volume,
            )
        )
    return tuple(ticks)


def parse_control_frame(raw: str) -> PingPong | SubscribeResult:
    """JSON 제어 프레임을 PINGPONG과 구독 ACK/NACK으로 가른다."""
    try:
        payload = json.loads(raw)
        header = payload["header"]
        tr_id = header["tr_id"]
    except (ValueError, KeyError, TypeError):
        raise FrameContractError("control frame is not the expected JSON shape") from None

    if tr_id == "PINGPONG":
        return PingPong(raw=raw)

    body = payload.get("body") or {}
    return SubscribeResult(
        tr_id=tr_id,
        tr_key=header.get("tr_key", ""),
        ok=body.get("rt_cd") == "0",
        code=body.get("msg_cd", ""),
        message=body.get("msg1", ""),
    )
