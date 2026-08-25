"""KIS 해외지수 분봉 수집기. S&P500·나스닥 종합 현물의 마감 1분봉을 `index_bar`에 쌓는다.

`kis.py`의 국내 분봉 수집과 같은 인증·전송(`send_get`)·저장 틀을 쓰되 응답 칸이 다르다.
국내 업종 분봉은 `bstp_nmix_*`, 선물은 `futs_*`인데 해외지수는 `optn_*`이고 전일 종가는
`output1.ovrs_nmix_prdy_clpr`에 온다. 그래서 파서를 따로 둔다.

## 실측 (2026-08-22, 운영 앱키)

- `FID_COND_MRKT_DIV_CODE=N`(해외지수), `FID_HOUR_CLS_CODE=0`(정규장). `1`은 빈 응답이다.
- 한 번에 **102봉**, 최신순. 날짜 커서가 없어 "최근 102봉" 말고는 받을 것이 없다.
- **시각은 America/New_York 벽시계다.** 16:00 봉이 현물 마감에 해당하고 16:01~16:41은 값이
  거의 고정된 정산 구간 봉이며, 그 마지막 봉이 일봉 API가 주는 공식 종가와 같다
  (SPX 2026-08-21: 16:00 봉 7674.30, 16:41 봉 7674.37 = 공식 종가).
  **정산 구간 봉까지 전부 저장한다.** 브리핑은 심볼마다 마지막 봉 하나를 읽으므로
  (`quote_bar/select_latest_briefing_bars.sql`) 그래야 공식 종가가 브리핑에 실린다.
- 지수라 `cntg_vol`은 0이다.
- 모르는 코드에도 `rt_cd=0`에 0건으로 답한다(`.DJI`는 분봉 0건, `RUT`은 일봉도 0건). 그래서
  코드를 `OverseasIndex`로 좁혀 요청 전에 막는다.

설계 배경은 `docs/kis-overseas-index-close.md`(개발 문서 4)에 있다.
"""

import json
from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, SecretStr, ValidationError

from modules.collectors.kis import (
    INDEX_BAR_UPSERT,
    SOURCE,
    SOURCE_RECORD_INSERT,
    KisPayloadError,
    KisResultError,
    QuoteBar,
    _decimal,
    send_get,
)
from modules.db import Connection
from modules.upsert import execute_upserts

OVERSEAS_INDEX_CHART_PATH = "/uapi/overseas-price/v1/quotations/inquire-time-indexchartprice"
OVERSEAS_INDEX_CHART_TR_ID = "FHKST03030200"
SOURCE_KEY = "overseas_index_1m"

# N = 해외지수. X(환율)·I(국채)·S(금선물)는 쓰지 않는다.
MARKET_DIV_CODE = "N"
# 0 = 정규장. 1(시간외)은 빈 응답이다(실측).
HOUR_CLS_CODE = "0"

# KIS 해외지수 봉의 날짜·시각은 뉴욕 벽시계다. 저장은 UTC다.
US_EASTERN = ZoneInfo("America/New_York")

# 한 번에 오는 봉 수(실측). 문서의 국내 분봉 상한과 같다.
MAX_BARS_PER_REQUEST = 102


class OverseasIndex(StrEnum):
    """수집 대상 해외 현물 지수. 저장 식별자, KIS 코드, 한국어 이름을 한 줄에 묶는다.

    국내 `DomesticIndex`와 같은 꼴이다. 다우(`.DJI`)는 KIS 분봉이 0건이고 러셀2000(`RUT`)은
    아예 없어 여기 없다. 러셀 현물은 Yahoo `RUSSELL2000`이 받는다.
    """

    kis_code: str
    label: str

    def __new__(cls, symbol: str, kis_code: str, label: str) -> Self:
        member = str.__new__(cls, symbol)
        member._value_ = symbol
        member.kis_code = kis_code
        member.label = label
        return member

    SP500 = ("SP500", "SPX", "S&P500")
    # 나스닥100(`NDX`)이 아니라 종합이다. 선물(`NASDAQ100_FUT`)과 기초지수가 어긋나지만
    # 뉴스가 말하는 "나스닥"이 종합이라 그쪽을 택했다(2026-08-22 결정).
    NASDAQ = ("NASDAQ", "COMP", "나스닥 종합")


def us_session_date(now: datetime) -> date:
    """이 시각에 막 끝난 미국 세션의 날짜. 뉴욕 시계로 봐야 세션 하나가 한 날짜에 담긴다.

    `modules.briefing.market.us_session_date`와 같은 값이다. 수집기가 브리핑 모듈을
    import하지 않으려고 따로 두고 테스트가 둘을 대조한다.
    """
    return now.astimezone(US_EASTERN).date()


class KisOverseasRawBar(BaseModel):
    """`output2` 한 건. 값은 전부 문자열이다."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    business_date: str = Field(alias="stck_bsop_date")
    contract_hour: str = Field(alias="stck_cntg_hour")
    open: str = Field(alias="optn_oprc")
    high: str = Field(alias="optn_hgpr")
    low: str = Field(alias="optn_lwpr")
    close: str = Field(alias="optn_prpr")
    volume: str = Field(default="", alias="cntg_vol")


class KisOverseasChartHead(BaseModel):
    """`output1`. 전일 종가와 요청 코드가 여기 온다."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    name: str = Field(default="", alias="hts_kor_isnm")
    previous_close: str = Field(default="", alias="ovrs_nmix_prdy_clpr")
    # 요청한 코드가 되돌아온다. `.DJI`처럼 데이터가 없는 코드에는 빠져 있었다.
    code: str = Field(default="", alias="stck_shrn_iscd")


class KisOverseasChartPayload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    rt_cd: str = ""
    msg_cd: str = ""
    msg1: str = ""
    output1: KisOverseasChartHead = KisOverseasChartHead()
    output2: tuple[KisOverseasRawBar, ...] = ()


class OverseasIndexFetch(BaseModel):
    """한 지수의 조회 결과. 파싱까지 끝난 봉만 든다."""

    model_config = ConfigDict(frozen=True)

    index: OverseasIndex
    session_date: date
    name: str
    bars: tuple[QuoteBar, ...]
    status: int
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @property
    def latest_bar_at(self) -> datetime:
        return self.bars[-1].bar_at


def parse_overseas_index_bars(
    body: bytes, index: OverseasIndex, session_date: date
) -> tuple[tuple[QuoteBar, ...], str]:
    """응답에서 (오름차순 봉, 지수 이름)을 뽑는다.

    **묵은 봉은 실패다.** 이 API는 날짜 커서가 없어 "최근 102봉"을 주는데, 그게 기대한
    세션(`session_date`)이 아니면 제공처가 갱신을 멈췄거나 우리가 엉뚱한 시각에 부른 것이다.
    어느 쪽이든 어제 봉을 오늘 것처럼 저장하는 것보다 멈추는 편이 낫다.
    """
    try:
        payload = KisOverseasChartPayload.model_validate_json(body)
    except ValidationError as error:
        raise KisPayloadError("KIS response is not a valid overseas index chart") from error

    if payload.rt_cd and payload.rt_cd != "0":
        raise KisResultError(payload.msg_cd, payload.msg1.strip())
    if not payload.output2:
        raise KisPayloadError(f"KIS returned an empty chart for {index.value} ({index.kis_code})")

    returned_code = payload.output1.code.strip()
    if returned_code and returned_code != index.kis_code:
        raise KisPayloadError(f"KIS answered {returned_code!r} for a {index.kis_code!r} request")

    previous_close = _decimal(payload.output1.previous_close or "0", "previous close")
    if not previous_close:
        raise KisPayloadError(f"KIS response for {index.value} has no previous close")

    expected_date = session_date.strftime("%Y%m%d")
    bars: list[QuoteBar] = []
    for raw in payload.output2:
        business_date = raw.business_date.strip()
        if business_date != expected_date:
            raise KisPayloadError(
                f"KIS returned {index.value} bars dated {business_date} but session {expected_date} was expected"
            )
        stamp = f"{business_date}{raw.contract_hour.strip()}"
        try:
            bar_at = datetime.strptime(stamp, "%Y%m%d%H%M%S").replace(tzinfo=US_EASTERN).astimezone(UTC)
        except ValueError as error:
            raise KisPayloadError(f"KIS returned an unparsable timestamp: {stamp!r}") from error

        volume = raw.volume.strip()
        try:
            bars.append(
                QuoteBar(
                    bar_at=bar_at,
                    open=_decimal(raw.open, "open"),
                    high=_decimal(raw.high, "high"),
                    low=_decimal(raw.low, "low"),
                    close=_decimal(raw.close, "close"),
                    volume=int(volume) if volume else None,
                    previous_close=previous_close,
                )
            )
        except ValidationError as error:
            raise KisPayloadError("KIS returned an invalid bar") from error

    # 응답은 최신순이다. 저장 순서를 다른 수집기와 맞춘다.
    bars.sort(key=lambda bar: bar.bar_at)
    return tuple(bars), payload.output1.name.strip()


class KisOverseasIndexCollector:
    """KIS 해외지수 분봉 수집기. 자격 증명과 토큰을 들고 지수마다 조회·저장한다.

    한 실행이 객체 하나다. 토큰은 발급 횟수 제한이 있어 DAG이 한 번 받아 넘긴다. 토큰은 이
    객체가 사는 동안 안 변하는 값이라 갈아 끼우지 않는다 — 401로 다시 받았으면 DAG이 새
    토큰으로 객체를 다시 만든다.

    파싱(`parse_overseas_index_bars`)은 밖에 둔다. 자격 증명도 연결도 보지 않는 순수 변환이다.
    """

    def __init__(self, token: SecretStr, app_key: SecretStr, app_secret: SecretStr) -> None:
        self._token = token
        self._app_key = app_key
        self._app_secret = app_secret

    def fetch(self, index: OverseasIndex, session_date: date) -> OverseasIndexFetch:
        """한 지수의 최근 102봉을 받아 파싱까지 끝낸다. HTTP·네트워크 오류는 `send_get`이 올린다."""
        started_at = datetime.now(UTC)
        body, status, _headers = send_get(
            self._token,
            self._app_key,
            self._app_secret,
            OVERSEAS_INDEX_CHART_PATH,
            OVERSEAS_INDEX_CHART_TR_ID,
            {
                "FID_COND_MRKT_DIV_CODE": MARKET_DIV_CODE,
                "FID_INPUT_ISCD": index.kis_code,
                "FID_HOUR_CLS_CODE": HOUR_CLS_CODE,
                "FID_PW_DATA_INCU_YN": "Y",
            },
        )
        completed_at = datetime.now(UTC)
        bars, name = parse_overseas_index_bars(body, index, session_date)
        return OverseasIndexFetch(
            index=index,
            session_date=session_date,
            name=name,
            bars=bars,
            status=status,
            started_at=started_at,
            completed_at=completed_at,
        )

    def store(self, connection: Connection, fetch: OverseasIndexFetch) -> int:
        """한 지수의 봉을 저장하고 저장한 봉 수를 돌려준다. 계보 레코드는 조회 1회에 1행이다."""
        metadata = {
            "symbol": fetch.index.value,
            "kis_code": fetch.index.kis_code,
            "name": fetch.name,
            "session_date": fetch.session_date.isoformat(),
            "interval": "1m",
            "bar_count": len(fetch.bars),
            "latest_bar_at": fetch.latest_bar_at.isoformat(),
            "status": fetch.status,
        }
        with connection.cursor() as cursor:
            cursor.execute(
                SOURCE_RECORD_INSERT,
                (
                    "api",
                    SOURCE,
                    SOURCE_KEY,
                    fetch.started_at,
                    fetch.completed_at,
                    "succeeded",
                    len(fetch.bars),
                    # 원본은 남기지 않는다. 봉 자체가 전부이고 날마다 쌓으면 계보가 수집보다 빨리 커진다.
                    None,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            source_record_id = cursor.fetchone()[0]
            execute_upserts(
                cursor,
                INDEX_BAR_UPSERT,
                [
                    (
                        SOURCE,
                        fetch.index.value,
                        bar.bar_at,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.volume,
                        bar.previous_close,
                        source_record_id,
                    )
                    for bar in fetch.bars
                ],
            )
        return len(fetch.bars)
