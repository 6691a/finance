"""한국투자증권 API에서 증권사 애널리스트의 종목 투자의견·목표주가를 수집한다.

뉴스는 "무슨 일이 있었다"까지고, 그 사건이 종목 가치에 어떤 뜻인지는 애널리스트가 쓴다.
이 모듈은 그 판단 중 **숫자**(투자의견, 목표주가, 괴리율)만 받는다. 리포트 본문은 KIS에
없다. 글은 `collectors/document/naver_research.py`가 문서로 흡수하고, 추론 툴이 둘을
발표일·증권사로 이어 읽는다. 설계는 `docs/analysis/market-thesis/6-analyst.md`다.

토큰 발급과 HTTP는 `collectors/kis.py`가 갖고 있어 그대로 쓴다. 모듈을 나눈 것은 스케줄과
실패 성격이 다르기 때문이다 — 포지션 지표는 전 영업일 확정치라 화~토 아침에 받지만,
투자의견은 **당일 아침 사건**이라 월~금 장전에 받아야 한다.

저장 대상은 `stock_analyst_opinion`이다. 정의의 원본은 백엔드의 `apps/models/market.py`이며
여기 SQL의 컬럼 이름은 `tests/collectors/test_kis_analyst_opinion.py`가 그 모델 metadata와
대조한다.

## 클래스인 이유

**자격 증명과 토큰이 상태다.** 함수로 두면 종목마다 `token, app_key, app_secret`을 다시
넘겨야 하고 DAG이 그 셋을 들고 다녀야 한다. 수집기 하나가 객체 하나이고, 종목 순회는
`fetch(stock_code, ...)` 반복이다. 상태가 필요 없는 것(응답 칸 파싱)은 같은 클래스의
`@staticmethod`나 모듈 함수로 둔다 — 감쌀 상태가 없는 것을 클래스로 만들지 않는다.

이 형태가 수집기 전체의 목표 구조다. 나머지 수집기의 전환 계획은
`docs/convention/collectors-class-migration.md`에 있다.

아래 계약은 2026-08-22에 운영 키로 확인했다.

## 응답의 모양

`invest-opinion`(`FHKST663300C0`)은 종목코드와 날짜 구간을 받아 그 구간의 투자의견을
증권사별 한 행으로 준다. 공개 예제 매핑에 없던 `mbcr_name`(증권사 약칭)이 실제 응답에
있어 자연키에 들어간다. 30일 구간에서 연속조회(`tr_cont`)는 발생하지 않았다.

- `invt_opnn`은 증권사마다 표기가 다르다. 같은 응답 안에 `BUY`와 `매수`가 섞여 있었다.
  그대로 저장하고 기계 판독은 `invt_opnn_cls_code`로 한다.
- 괴리 값이 두 벌 온다. `stck_nday_esdg`·`nday_dprt`는 **발표 전일 종가** 대비라 고정값이고,
  `stft_esdg`·`dprt`는 **조회 시점 현재가** 대비라 매일 바뀐다(실측: 전자는
  `stck_prdy_clpr - hts_goal_prc`, 후자는 조회 당시 현재가 - 목표가). 발표일 행에 조회
  시점 값을 섞으면 upsert마다 과거 행이 조용히 바뀌므로 **전자만 저장한다.**
- `invest-opbysec`(`FHKST663400C0`)도 종목코드를 받고 같은 행에 조회 시점 현재가를 더해
  줄 뿐이라 쓰지 않는다.

## 종목 목록은 Enum이 아니라 DB다

`kis_positioning.PositioningStock`과 달리 `instrument.is_watched`를 읽는다. 추론 대상
(`thesis.subjects`)과 같은 SQL이라 추적 종목이 늘 때 이 모듈을 고치지 않는다.

**주의:** 그 SQL은 `market`을 거르지 않는다. 해외 상장 종목이 `is_watched`가 되는 날 KIS
국내 API가 `rt_cd != 0`으로 답해 DAG가 실패한다. 지금 추적 종목은 둘 다 코스피라 그대로
두고, 그날이 오면 `select_watched_krx.sql` 하나로 `thesis.subjects`와 함께 고친다.
"""

import json
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

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

OPINION_PATH = "/uapi/domestic-stock/v1/quotations/invest-opinion"
OPINION_TR_ID = "FHKST663300C0"
OPINION_SCREEN = "16633"
OPINION_SOURCE_KEY = "invest_opinion"

# 국내주식 상품 구분. 거래장 selector가 아니다.
DOMESTIC_STOCK_DIVISION = "J"

# 응답 헤더 `tr_cont`가 이 값이면 다음 장이 있다. 잘린 응답을 조용히 저장하지 않는다.
CONTINUATION_MARKERS = frozenset({"M", "F"})

OPINION_UPSERT = read_sql("postgres", "stock_analyst_opinion", "upsert.sql")
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
WATCHED_INSTRUMENTS = read_sql("postgres", "instrument", "select_watched.sql")


def _day(value: Any, field: str) -> date:
    """`YYYYMMDD`. `strptime`은 naive datetime을 만들어 쓰지 않는다."""
    text = str(value if value is not None else "").strip()
    if len(text) != 8 or not text.isdigit():
        raise KisPayloadError(f"{field} must be YYYYMMDD, got {value!r}")
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        raise KisPayloadError(f"{field} is not a real date: {value!r}") from None


def _decimal(value: Any, field: str) -> Decimal:
    """금액·비율 한 칸. 공백 패딩과 쉼표가 붙어 올 수 있고 음수는 정상값이다."""
    text = str(value if value is not None else "").strip().replace(",", "")
    if not text or text == "-":
        return Decimal(0)
    try:
        return Decimal(text)
    except InvalidOperation:
        raise KisPayloadError(f"KIS returned a non-numeric {field}: {value!r}") from None


def _text(value: Any, field: str) -> str:
    """비어 있으면 안 되는 문자열 칸. 증권사 이름이 비면 자연키가 깨진다."""
    text = str(value if value is not None else "").strip()
    if not text:
        raise KisPayloadError(f"KIS returned an empty {field}")
    return text


class OpinionRow(BaseModel):
    """증권사 하나의 종목 투자의견 한 건."""

    model_config = ConfigDict(frozen=True)

    business_date: date
    broker_name: str
    opinion: str
    opinion_code: str
    previous_opinion: str
    previous_opinion_code: str
    target_price: Decimal
    previous_close: Decimal
    gap_amount: Decimal
    gap_rate: Decimal

    @classmethod
    def from_payload(cls, row: dict[str, Any]) -> "OpinionRow":
        return cls(
            business_date=_day(row.get("stck_bsop_date"), "stck_bsop_date"),
            broker_name=_text(row.get("mbcr_name"), "mbcr_name"),
            opinion=_text(row.get("invt_opnn"), "invt_opnn"),
            opinion_code=_text(row.get("invt_opnn_cls_code"), "invt_opnn_cls_code"),
            previous_opinion=_text(row.get("rgbf_invt_opnn"), "rgbf_invt_opnn"),
            previous_opinion_code=_text(row.get("rgbf_invt_opnn_cls_code"), "rgbf_invt_opnn_cls_code"),
            target_price=_decimal(row.get("hts_goal_prc"), "hts_goal_prc"),
            previous_close=_decimal(row.get("stck_prdy_clpr"), "stck_prdy_clpr"),
            # 조회 시점 현재가 대비(`stft_esdg`, `dprt`)는 저장하지 않는다. 모듈 docstring 참고.
            gap_amount=_decimal(row.get("stck_nday_esdg"), "stck_nday_esdg"),
            gap_rate=_decimal(row.get("nday_dprt"), "nday_dprt"),
        )


class Fetch(BaseModel):
    """조회 한 번. 저장에 쓸 행과 계보에 남길 값을 함께 담는다."""

    model_config = ConfigDict(frozen=True)

    source_key: str
    stock_code: str
    rows: tuple[OpinionRow, ...]
    metadata: dict[str, Any]
    started_at: datetime
    completed_at: datetime


def watched_stocks(connection: Connection) -> tuple[tuple[str, str], ...]:
    """수집 대상 `(종목코드, 이름)`. 추론 대상과 같은 SQL이다(모듈 docstring).

    수집기 클래스 밖에 두는 것은 이 조회가 KIS와 무관하기 때문이다. 자격 증명도 토큰도
    필요 없고 마스터 테이블만 본다.
    """
    with connection.cursor() as cursor:
        cursor.execute(WATCHED_INSTRUMENTS)
        return tuple((str(row[0]), str(row[1])) for row in cursor.fetchall())


class KisAnalystOpinionCollector:
    """KIS 종목투자의견 수집기. 자격 증명과 토큰을 들고 종목마다 조회·저장한다.

    한 실행이 객체 하나다. 토큰은 발급 횟수 제한이 있어 DAG이 한 번 받아 넘긴다.
    """

    def __init__(self, token: SecretStr, app_key: SecretStr, app_secret: SecretStr) -> None:
        self._token = token
        self._app_key = app_key
        self._app_secret = app_secret

    def fetch(self, stock_code: str, observation_start: date, observation_end: date) -> Fetch:
        """종목 하나의 투자의견을 구간으로 받는다. 증권사별 한 행이다."""
        started_at = datetime.now(UTC)
        payload = self._call(
            OPINION_PATH,
            OPINION_TR_ID,
            {
                "FID_COND_MRKT_DIV_CODE": DOMESTIC_STOCK_DIVISION,
                "FID_COND_SCR_DIV_CODE": OPINION_SCREEN,
                "FID_INPUT_ISCD": stock_code,
                "FID_INPUT_DATE_1": observation_start.strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": observation_end.strftime("%Y%m%d"),
            },
        )
        try:
            rows = tuple(OpinionRow.from_payload(row) for row in self._rows(payload, "output"))
        except (KeyError, ValidationError) as error:
            raise KisPayloadError(f"KIS invest opinion row is malformed: {error}") from None

        self._reject_future_rows(rows, observation_end)
        return Fetch(
            source_key=OPINION_SOURCE_KEY,
            stock_code=stock_code,
            rows=rows,
            metadata={
                "stock_code": stock_code,
                "observation_start": observation_start.isoformat(),
                "observation_end": observation_end.isoformat(),
                "returned": len(rows),
            },
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def store(self, connection: Connection, fetch: Fetch) -> int:
        """계보 한 건과 행들을 저장한다. 0건이어도 계보는 남긴다 — 조회했지만 없던 구간이다."""
        with connection.cursor() as cursor:
            source_record_id = self._insert_source_record(cursor, fetch, len(fetch.rows))
            execute_upserts(
                cursor,
                OPINION_UPSERT,
                [
                    (
                        fetch.stock_code,
                        row.business_date,
                        row.broker_name,
                        row.opinion,
                        row.opinion_code,
                        row.previous_opinion,
                        row.previous_opinion_code,
                        row.target_price,
                        row.previous_close,
                        row.gap_amount,
                        row.gap_rate,
                        source_record_id,
                    )
                    for row in fetch.rows
                ],
            )
        return len(fetch.rows)

    # --- 내부 -----------------------------------------------------------------

    def _call(self, path: str, tr_id: str, query: dict[str, str]) -> dict[str, Any]:
        body, _, headers = send_get(self._token, self._app_key, self._app_secret, path, tr_id, query)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as error:
            raise KisPayloadError(f"KIS returned a non-JSON body: {error}") from None
        if not isinstance(payload, dict):
            raise KisPayloadError("KIS returned a JSON body that is not an object")

        code = str(payload.get("rt_cd", ""))
        if code != "0":
            raise result_error(code, str(payload.get("msg1", "")).strip())

        # 잘린 응답은 실패다. 백필은 구간을 줄여 돌린다.
        if str(headers.get("tr_cont", "")).strip() in CONTINUATION_MARKERS:
            raise KisPayloadError("KIS invest opinion response is truncated (tr_cont); narrow the window")
        return payload

    @staticmethod
    def _rows(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
        output = payload.get(key) or []
        if isinstance(output, dict):
            return [output]
        if not isinstance(output, list):
            raise KisPayloadError(f"KIS returned a {key} that is neither a list nor an object")
        return output

    @staticmethod
    def _reject_future_rows(rows: Sequence[OpinionRow], observation_end: date) -> None:
        """요청 종료일보다 뒤인 행은 받지 않는다. 구간을 잘못 보냈다는 뜻이다."""
        future = [row.business_date for row in rows if row.business_date > observation_end]
        if future:
            raise KisPayloadError(f"KIS invest opinion returned rows after {observation_end}: {sorted(future)[:3]}")

    @staticmethod
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
