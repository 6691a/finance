"""OpenDART에서 공시와 실적을 수집한다.

**대상은 코드가 아니라 DB가 정한다.** `instrument.filing_entity_id`가 채워진 행이 공시와
분기 실적의 대상이고 `filing_entities`가 그것을 읽는다. `is_watched`(시세 대상)와 다른
축이다 — 한 플래그에 묶어 두면 공시 대상을 늘릴 때 분봉·수급·실시간 구독까지 끌려온다.

산업 대표 20사를 대상으로 두는 이유는 개별 기업 분석이 아니라 **한국 거시 지표**다.
설계는 `docs/collection/korea-industry-macro-expansion.md`에 있다.

저장 대상은 `disclosure_event`와 `earnings_fact`다. 정의의 원본은 백엔드의
`apps/models/market.py`이며 여기 SQL의 컬럼 이름은 `tests/collectors/test_dart.py`가 그 모델
metadata와 대조한다.

아래 계약은 2026-08-12에 실제 응답으로 확인했다.

## 공시 이벤트와 실적 추출을 분리한다

공시 이벤트 저장이 먼저고 숫자 추출은 그다음이다. 잠정실적 표 형식이 바뀌어 숫자를 못
읽어도 **공시 이벤트까지 버리지 않는다.** 그래서 `earnings_fact`는 `disclosure_event`로
외래키를 걸지 않고 `rcept_no`로만 이어진다. 아직 숫자를 못 얻은 공시는 다음 폴링이
다시 시도하므로 별도 작업 큐가 필요 없다.

## 시각 셋이 다른 것을 뜻한다

- `rcept_dt`는 `YYYYMMDD` 날짜뿐이다. 시·분이 없다. 자정으로 꾸며 저장하지 않는다.
- `detected_at`은 우리가 처음 본 시각이고 재수집해도 갱신하지 않는다. 2분 폴링이라 공시
  시각의 상한이며, 오차는 폴링 주기와 DART 목록 반영 지연의 합이다.
- **분 단위 접수 시각은 수집하지 않는다.** 그 값은 공식 RSS(`dart.fss.or.kr`)에만 있는데
  전 상장사 최신 50건뿐이라 실측에서 1시간 35분치만 덮었고, 우리가 저장한 공시 52건과
  겹치는 접수번호가 하나도 없었다. 과거는 원리적으로 채울 수 없고 현재는 `detected_at`이
  이미 2분 해상도를 준다. 매 폴링에 호출 하나와 계보 행 하나를 더할 값어치가 없다.
  DART 목록 반영 지연 자체를 재야 할 때 회사별 RSS를 그때 붙인다.

## 잠정실적 원문 (실측)

`document.xml`은 ZIP이고 안에 `<접수번호>.xml` 하나가 들어 있다. HTML 표다.

- 본문 표는 **`XFormD1_Form0_RepeatTable0`**이고 실적기간 표는 `XFormD1_Form0_Table0`다.
  **정정 공시에는 `XFormD8_*` 표가 앞에 더 붙는다.** 그 표는 정정 사유와 정정 전후 요약이라
  숫자를 거기서 읽으면 안 된다. 그래서 표를 id로 집는다. 위치로 찾으면 정정 공시에서 조용히
  다른 숫자를 읽는다.
- 지표 행은 `매출액`·`영업이익`·`당기순이익`이고 각각 `당해실적`(그 분기)과 `누계실적`
  (사업연도 누계) 두 줄이다. 이어지는 값 일곱 칸은
  `[당기실적, 전기실적, 전기대비 증감율, 흑자적자전환, 전년동기실적, 전년동기 증감율, 흑자적자전환]`이다.
- **`당기순이익` 바로 아래 `지배기업 소유주지분 순이익` 행이 있다.** 앞부분만 맞춰 찾으면
  그 행을 순이익으로 읽는다. 라벨은 정확히 일치시킨다.
- **단위가 공시마다 다르다.** 실측에서 같은 분기에 삼성전자 원본이 `억원`, 그 정정이 `조원`,
  SK하이닉스가 `백만원`이었다. 표 안의 `단위 : ...` 칸을 읽어 원 단위로 정규화한다.
  정정본이 조원으로 반올림해 원본보다 정밀도가 낮아지는 일이 있는데, 그건 공시가 그런 것이라
  그대로 저장한다.
- 값에 쉼표가 들어가고 없는 값은 `-`다. `-`를 0으로 바꾸지 않는다.

## 정기 재무제표 (실측)

`fnlttSinglAcntAll.json`은 접수번호가 아니라 회사·사업연도·보고서코드로 조회한다. 응답
`rcept_no`가 처리 중인 공시와 다르면 저장하지 않는다.

- 손익계산서는 `sj_div='IS'`다. `CIS`에도 순이익이 중복으로 오므로 `IS`만 읽는다.
- **계정은 이름이 아니라 `account_id`로 잡는다.** 순이익의 `account_nm`은 보고서에 따라
  `반기순이익`·`분기순이익`·`당기순이익`으로 바뀌지만 id는 `ifrs-full_ProfitLoss`로 같다.
- `thstrm_amount`가 그 분기 금액이고 `thstrm_add_amount`가 사업연도 누계다.
- 전년 동기는 누계(`frmtrm_add_amount`)만 오고 분기값(`frmtrm_amount`)은 비어 있었다.
- 금액 단위는 원이고 `currency`는 `KRW`다. 배수를 곱하지 않는다.

`report_nm`이 `분기보고서 (2026.03)` 꼴이라 사업연도와 보고서코드를 여기서 뽑는다.
`[기재정정]` 같은 접두사는 떼고 판정하되 원문 이름은 그대로 저장한다.

## 다중회사 주요계정 (실측 2026-09-04)

`fnlttMultiAcnt.json`은 회사 고유번호를 콤마로 이어 받는다. 20사를 한 번에 물으면 621행이
오고 회사가 스무 곳 다 들어 있다. `fnlttSinglAcntAll`이 회사마다 호출인 것과 갈리는 지점이고,
거시 표본을 스무 곳으로 넓히면서도 하루 4콜로 끝나는 이유다.

대신 계약이 셋 다르다.

- **`account_id`가 없다.** 계정을 이름으로 잡는다(`MULTI_ACCOUNT_METRICS`). 금융·증권사는
  `영업이익(손실)`로 오고 **삼성생명·KB금융은 매출액 행이 아예 없다.**
- **`당기순이익(손실)`이 손익계산서 안에서 두 번 온다**(전 회사). 값은 같아서 하나만 남기고,
  **다르면 실패시킨다** — 어느 줄이 맞는지 고를 수 없다.
- **연결과 별도가 한 응답에 함께 온다.** `fs_div`로 갈려 오므로 범위를 지정해 두 번 부르지
  않고 둘 다 저장한다.

기간은 `thstrm_dt`가 `2025.01.01 ~ 2025.09.30` 꼴로 준다. **끝 날짜가 기간 종료일**이고
`thstrm_amount`가 3개월치, `thstrm_add_amount`가 사업연도 누계다. 사업보고서(`11011`)에는
누계 칸이 없고 연간치가 `thstrm_amount`로 온다.
"""

import hashlib
import io
import json
import logging
import re
import zipfile
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from http.client import HTTPException
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, SecretStr
from scrapling import Selector

from modules.db import Connection, Cursor
from modules.sql import read_sql
from modules.upsert import execute_upserts

logger = logging.getLogger(__name__)

OPENDART_BASE_URL = "https://opendart.fss.or.kr/api"

SOURCE = "dart"
SOURCE_KEY_LIST = "disclosure_list"
SOURCE_KEY_DOCUMENT = "disclosure_document"
SOURCE_KEY_FINANCIALS = "financial_statement"
# 다중회사 주요계정은 조회 한 번이 회사 스무 곳을 담는다. 계보도 그 조회 단위로 남긴다.
SOURCE_KEY_MULTI_ACCOUNT = "multi_account"

REQUEST_TIMEOUT_SECONDS = 30

# OpenDART(`opendart.fss.or.kr`)는 기본 User-Agent로도 답한다.
USER_AGENT = "news-collector/1.0 (+https://opendart.fss.or.kr/)"

# 목록 한 장의 최대 건수와 장 수 상한. 7일 창에서는 한 장이면 끝나지만 정정이 몰리면 늘어난다.
# 상한은 백필이 정한다. 삼성전자의 1년치가 2,886건(실측)이라 열 장으로는 절반도 못 받는다.
PAGE_COUNT = 100
MAX_PAGES = 40

# OpenDART가 본문 `status`로 알리는 값 중 우리가 뜻을 아는 것.
STATUS_OK = "000"
STATUS_NO_DATA = "013"
STATUS_NO_FILE = "014"
STATUS_RATE_LIMIT = "020"

# 원문 표의 id. 위치가 아니라 id로 집는다. 정정 공시에는 앞에 다른 표가 더 붙는다.
PERIOD_TABLE_ID = "XFormD1_Form0_Table0"
RESULT_TABLE_ID = "XFormD1_Form0_RepeatTable0"

# 지표 행 라벨. `당기순이익` 아래 `지배기업 소유주지분 순이익`이 있어 정확히 일치시킨다.
PROVISIONAL_METRICS: dict[str, str] = {
    "매출액": "revenue",
    "영업이익": "operating_profit",
    "당기순이익": "net_income",
}

# 지표 행 다음에 오는 기간 기준 라벨.
BASIS_LABELS: dict[str, str] = {"당해실적": "period", "누계실적": "cumulative"}

# 라벨 뒤에 이어지는 값 칸 수. 사이트가 열을 늘리면 값이 옆으로 밀리므로 개수를 검증한다.
PROVISIONAL_VALUE_COLUMNS = 7
CURRENT_VALUE_INDEX = 0
PRIOR_YEAR_VALUE_INDEX = 4

# 원문이 밝히는 금액 단위. 원 단위로 정규화할 때 쓴다.
UNIT_MULTIPLIERS: dict[str, Decimal] = {
    "원": Decimal(1),
    "천원": Decimal(1_000),
    "백만원": Decimal(1_000_000),
    "십억원": Decimal(1_000_000_000),
    "억원": Decimal(100_000_000),
    "조원": Decimal(1_000_000_000_000),
}

# 정기 재무제표 계정. 이름이 아니라 id로 잡는다.
FINANCIAL_ACCOUNTS: dict[str, str] = {
    "ifrs-full_Revenue": "revenue",
    "dart_OperatingIncomeLoss": "operating_profit",
    "ifrs-full_ProfitLoss": "net_income",
}

# 다중회사 주요계정(`fnlttMultiAcnt`)의 손익계산서 계정. **이 API는 `account_id`를 주지
# 않는다**(2026-09-04 실측). 단일회사 전체 재무제표(`fnlttSinglAcntAll`)와 갈리는 지점이고
# 그래서 여기서만 이름으로 잡는다. 이름이 약한 키라는 것은 사실이고, 그 약함을 이 표가 좁힌다.
#
# **금융·증권사는 `영업이익(손실)`로 온다**(미래에셋증권·삼성생명 실측). 그리고 **매출액 행이
# 아예 없는 회사가 있다**(삼성생명·KB금융) — 0으로 채우지 않고 행을 만들지 않는다.
#
# 표에 없는 이름(`법인세차감전 순이익`·`총포괄손익`·`이자수익`·`순이자손익`·`순수수료손익`·
# `영업비용`·`이자비용`)은 버린다.
MULTI_ACCOUNT_METRICS: dict[str, str] = {
    "매출액": "revenue",
    "영업이익": "operating_profit",
    "영업이익(손실)": "operating_profit",
    "당기순이익(손실)": "net_income",
}

# 다중회사 주요계정의 기간 기준과 그것을 담은 응답 칸.
#
# 분기·반기 보고서는 `thstrm_amount`가 3개월치이고 `thstrm_add_amount`가 사업연도 누계다
# (2026-09-04 실측: 삼성전자 2025 3분기 매출 86조 / 누계 239조). **사업보고서(`11011`)에는
# 누계 칸이 아예 없고** 연간치가 `thstrm_amount`로 온다 — 그래서 사업보고서 행은 `period`
# 하나로만 저장되고 그 `period_end`가 12-31이다.
MULTI_ACCOUNT_BASES: tuple[tuple[str, str, str], ...] = (
    ("period", "thstrm_amount", "frmtrm_amount"),
    ("cumulative", "thstrm_add_amount", "frmtrm_add_amount"),
)

# `2026.01.01 ~ 2026.03.31` 꼴. 끝 날짜가 기간 종료일이다. 재무상태표 행은
# `2026.03.31 현재`로 오지만 손익계산서만 읽으므로 여기 오지 않는다.
MULTI_ACCOUNT_PERIOD_PATTERN = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})\s*~\s*(\d{4})\.(\d{2})\.(\d{2})")

# 사업연도 분기 종료월 → OpenDART 보고서코드. `PERIODIC_REPORTS`와 같은 표인데, 이쪽은
# 보고서 이름이 아니라 달력에서 기간을 되짚어 만든다.
QUARTER_REPORT_CODES: tuple[tuple[int, str], ...] = ((3, "11013"), (6, "11012"), (9, "11014"), (12, "11011"))

# 한 실행이 훑는 보고서 수. 직전 네 분기를 매번 다시 본다. 정정 공시로 숫자가 바뀌면
# 새 접수번호로 오므로 그것을 집으려면 지나간 기간도 계속 봐야 한다.
MULTI_ACCOUNT_PERIODS = 4

# 정기보고서 이름의 월 → OpenDART 보고서코드와 기간 종료월.
PERIODIC_REPORTS: dict[int, str] = {3: "11013", 6: "11012", 9: "11014", 12: "11011"}
QUARTER_END_DAYS: dict[int, int] = {3: 31, 6: 30, 9: 30, 12: 31}

# 본문을 받을 공시 종류. 보고서명에 이 조각이 들어가면 받는다.
#
# **화이트리스트다.** 전에는 임원 지분 신고 하나만 빼는 블랙리스트였는데 그것으로 부족했다 —
# 4,014건 중 3,682건이 그 하나였지만, 남은 332건도 대부분 형식 공시(지급수단별·동일인등
# 거래변경·정기보고서)라 인과 사건이 아니었다(2026-08-28 실측).
#
# **정기보고서를 넣지 않는 이유는 크기다.** 삼성전자 반기보고서 원문이 638,116자다
# (조회공시요구는 220자). 한 건이 프롬프트 예산을 통째로 먹고 그 내용은 사건도 아니다.
#
# 조각으로 거는 것은 이름이 접두·접미로 늘어나기 때문이다 — `[기재정정]주요사항보고서
# (유상증자결정)`, `주요사항보고서(자기주식취득결정)`이 한 조각에 걸린다.
MATERIAL_REPORT_KEYWORDS: tuple[str, ...] = (
    "조회공시",  # 거래소가 풍문·보도를 묻는다. 그 답변까지 한 쌍이다
    "손실발생",  # 파생상품거래손실발생
    "주요사항보고서",  # 자기주식·유상증자·합병 등
    "자기주식",
    "배당",
    "증자",
    "대량보유",
    "소유주식변동",  # 최대주주등소유주식변동신고서
    # **`실적`이 아니라 `(잠정)실적`이다.** 조각을 넓게 잡으면 `증권발행실적보고서`가 걸린다 —
    # 증권사가 ELS·DLS를 발행할 때마다 내는 형식 공시라 인과 사건이 아니고, 대상을 산업 대표
    # 20사로 넓히자 화이트리스트 통과분 84건 중 27건이 미래에셋증권의 그것 하나였다
    # (2026-08-05~09-03 실측). 잠정실적 공시는 `연결재무제표기준영업(잠정)실적(공정공시)`와
    # `영업(잠정)실적(공정공시)` 둘인데 이 조각이 둘 다 잡는다.
    "(잠정)실적",
    "기타경영사항",
)

# 한 실행이 받는 본문 수 상한. 2분 폴링이라 밀린 것은 다음 실행이 이어 받는다.
MAX_BODIES_PER_RUN = 20

# 본문에서 통째로 걷어낼 요소. 거래소 공시는 `<style>`에 CSS가 들어 있어 태그만 벗기면
# `.xforms * { font-family: 돋움체;}`가 본문 앞에 붙는다(2026-08-29 실측).
SCRIPT_OR_STYLE_PATTERN = re.compile(r"<(style|script)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
HTML_ENTITY_PATTERN = re.compile(r"&[a-zA-Z]+;|&#\d+;")

# 잠정실적 공시를 알아보는 이름. 정정 접두사를 떼고 비교한다.
PROVISIONAL_REPORT_NAME = "연결재무제표기준영업(잠정)실적(공정공시)"
PERIODIC_REPORT_PATTERN = re.compile(r"^(사업보고서|반기보고서|분기보고서)\s*\((\d{4})\.(\d{2})\)")
CORRECTION_PREFIX_PATTERN = re.compile(r"^\[[^\]]*\]\s*")

DISCLOSURE_EVENT_UPSERT = read_sql("postgres", "disclosure_event", "upsert.sql")
DISCLOSURE_EVENT_PENDING_SELECT = read_sql("postgres", "disclosure_event", "select_pending_earnings.sql")
DISCLOSURE_BODY_PENDING_SELECT = read_sql("postgres", "disclosure_event", "select_pending_bodies.sql")
DISCLOSURE_BODY_UPDATE = read_sql("postgres", "disclosure_event", "update_body.sql")
EARNINGS_FACT_UPSERT = read_sql("postgres", "earnings_fact", "upsert.sql")
SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
FILING_ENTITY_SELECT = read_sql("postgres", "instrument", "select_filing_entities.sql")


class FilingEntity(BaseModel):
    """규제 공시 수집 대상 한 곳. `instrument` 마스터의 한 행이다.

    **명단이 코드가 아니라 DB에 있다.** 전에는 `DartCompany` StrEnum 두 줄이었는데, 대상이
    스물이 되면서 Enum이 명단을 드는 자리가 아니게 됐다. `is_watched`(시세 대상)와도 갈랐다 —
    한 플래그에 묶어 두면 공시 대상을 늘릴 때 분봉·수급·실시간 구독까지 끌려온다.
    """

    model_config = ConfigDict(frozen=True)

    stock_code: str
    name: str
    filing_entity_id: str
    sector: str | None


def filing_entities(connection: Connection) -> tuple[FilingEntity, ...]:
    """공시·실적 수집 대상. 번호가 있다는 것이 곧 대상이라는 뜻이다.

    수집기 클래스 밖에 두는 것은 이 조회가 DART와 무관하기 때문이다 — 자격 증명도 토큰도
    필요 없고 마스터 테이블만 본다. `kis_opinion.watched_stocks`와 같은 자리다.
    """
    with connection.cursor() as cursor:
        cursor.execute(FILING_ENTITY_SELECT)
        rows = cursor.fetchall() or []
    return tuple(
        FilingEntity(
            stock_code=str(row[0]),
            name=str(row[1]),
            filing_entity_id=str(row[2]),
            sector=str(row[3]) if row[3] is not None else None,
        )
        for row in rows
    )

class DartHTTPError(RuntimeError):
    """OpenDART가 2xx가 아닌 상태로 응답했다. 재시도 여부는 호출자가 `status`로 정한다."""

    def __init__(self, status: int) -> None:
        super().__init__(f"DART request failed with HTTP {status}")
        self.status = status


class DartStatusError(RuntimeError):
    """OpenDART가 본문 `status`로 실패를 알렸다.

    HTTP는 200이다. 재시도 여부는 DAG가 `code`로 정한다. 데이터 없음(`013`)과 원문 없음
    (`014`)은 여기까지 오지 않고 수집기가 빈 결과로 바꾼다.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"DART returned {code}: {message}")
        self.code = code
        self.message = message


class DartPayloadError(ValueError):
    """응답이 계약을 지키지 않았다. 재시도해도 같은 결과다."""


class Disclosure(BaseModel):
    """공시 목록의 한 행."""

    model_config = ConfigDict(frozen=True)

    corp_code: str
    corp_name: str
    stock_code: str
    corp_class: str
    report_name: str
    rcept_no: str
    filer_name: str
    receipt_date: date
    remarks: str | None

    @classmethod
    def from_payload(cls, row: dict[str, Any]) -> "Disclosure":
        remarks = str(row.get("rm", "")).strip()
        return cls(
            corp_code=row["corp_code"],
            corp_name=str(row["corp_name"]).strip(),
            stock_code=str(row["stock_code"]).strip(),
            corp_class=str(row["corp_cls"]).strip(),
            # 원문 이름은 뒤에 공백 패딩이 붙어 온다. 의미가 아니라 표기라 잘라 낸다.
            report_name=str(row["report_nm"]).strip(),
            rcept_no=str(row["rcept_no"]).strip(),
            filer_name=str(row["flr_nm"]).strip(),
            receipt_date=_day(row["rcept_dt"]),
            remarks=remarks or None,
        )


class DisclosureFetch(BaseModel):
    model_config = ConfigDict(frozen=True)

    company: str
    corp_code: str
    begin_date: date
    end_date: date
    disclosures: tuple[Disclosure, ...]
    page_count: int
    total_count: int
    started_at: datetime
    completed_at: datetime


class StatementScope(StrEnum):
    """재무제표 범위. 연결과 별도를 합치거나 서로 대체하지 않는다.

    `apps/models/market.py`의 같은 이름 Enum과 값이 같아야 한다. Airflow 트리는 `apps/`를
    보지 못해 한 벌을 더 두고, `tests/collectors/test_dart.py`가 둘을 대조한다.
    """

    CFS = "CFS"
    OFS = "OFS"


class ProvisionalMeta(BaseModel):
    """잠정실적 원문을 어떻게 읽었는지. `source_record.metadata`로 남아 재현의 근거가 된다."""

    model_config = ConfigDict(frozen=True)

    unit_multiplier: str
    statement_scope: StatementScope


class EarningsValue(BaseModel):
    """실적 한 칸. `earnings_fact` 한 행이 된다."""

    model_config = ConfigDict(frozen=True)

    metric: str
    amount_basis: str
    statement_scope: str
    period_end: date
    current_amount: Decimal
    prior_year_amount: Decimal | None
    currency: str
    source_account_id: str | None
    source_account_name: str


class EarningsFetch(BaseModel):
    """실적 조회 한 번. 계보에 남길 값과 추출한 지표를 함께 담는다."""

    model_config = ConfigDict(frozen=True)

    rcept_no: str
    stock_code: str
    release_type: str
    values: tuple[EarningsValue, ...]
    metadata: dict[str, Any]
    started_at: datetime
    completed_at: datetime


class MultiAccountEntry(BaseModel):
    """다중회사 주요계정 응답에서 뽑은 한 회사·한 재무제표 범위. `earnings_fact` 여러 행이 된다.

    연결(`CFS`)과 별도(`OFS`)가 둘 다 오고 **한쪽을 고르지 않는다.** 자연키에
    `statement_scope`가 들어 있어 서로 덮지 않고, 어느 쪽을 볼지는 읽는 쪽이 정한다.
    """

    model_config = ConfigDict(frozen=True)

    stock_code: str
    rcept_no: str
    statement_scope: str
    values: tuple[EarningsValue, ...]


class MultiAccountFetch(BaseModel):
    """다중회사 주요계정 조회 한 번. **회사 스무 곳이 응답 하나에 들어 있다.**

    그래서 `source_record`도 회사가 아니라 이 조회 단위로 하나만 남는다.
    """

    model_config = ConfigDict(frozen=True)

    business_year: int
    report_code: str
    entries: tuple[MultiAccountEntry, ...]
    requested_count: int
    answered_count: int
    row_count: int
    started_at: datetime
    completed_at: datetime

    @property
    def value_count(self) -> int:
        return sum(len(entry.values) for entry in self.entries)


def _day(value: str) -> date:
    """`YYYYMMDD`. `strptime`은 naive datetime을 만들어 쓰지 않는다."""
    text = str(value).strip()
    if len(text) != 8 or not text.isdigit():
        raise DartPayloadError(f"rcept_dt must be YYYYMMDD, got {value!r}")
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:]))
    except ValueError:
        raise DartPayloadError(f"rcept_dt is not a real date: {value!r}") from None


def _amount(text: str, multiplier: Decimal = Decimal(1)) -> Decimal | None:
    """원문 금액 한 칸. 값이 없으면 `None`이다.

    `-`는 값 없음이고 0이 아니다. 괄호는 음수 표기이며 쉼표는 자릿수 구분이다.
    """
    cleaned = str(text).strip().replace(",", "").replace(" ", "")
    if cleaned in ("", "-", "–", "—"):
        return None
    negative = cleaned.startswith("(") and cleaned.endswith(")")
    if negative:
        cleaned = cleaned[1:-1]
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        raise DartPayloadError(f"amount is not a number: {text!r}") from None
    return -(amount * multiplier) if negative else amount * multiplier


def _get(url: str, params: dict[str, str] | None = None) -> bytes:
    """GET 한 번. **API 키가 질의 문자열에 들어가므로 URL을 예외나 로그에 남기지 않는다.**"""
    target = f"{url}?{urlencode(params)}" if params else url
    request = Request(target, headers={"user-agent": USER_AGENT})
    try:
        with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            return response.read()
    except HTTPError as error:
        raise DartHTTPError(error.code) from None
    except (URLError, HTTPException, OSError) as error:
        # 끊긴 연결(`RemoteDisconnected`)은 `URLError`가 아니라 그대로 올라온다. 재시도
        # 분류가 어긋나지 않게 여기서 한 종류로 모은다.
        raise ConnectionError(f"DART request failed: {error}") from None


def _json(raw: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DartPayloadError(f"DART returned a non-JSON body: {error}") from None
    if not isinstance(payload, dict):
        raise DartPayloadError("DART returned a JSON body that is not an object")
    return payload


def is_material(report_name: str) -> bool:
    """본문을 받을 종류인가. **정기보고서는 이름이 걸려도 뺀다.**

    `반기보고서 (2026.06)`에는 위 조각이 없지만, `[기재정정]사업보고서` 같은 이름이 나중에
    `실적` 조각에 걸릴 수 있다. 크기가 자릿수로 다르므로 여기서 한 번 더 막는다.
    """
    if periodic_report(report_name) is not None:
        return False
    return any(keyword in report_name for keyword in MATERIAL_REPORT_KEYWORDS)


def disclosure_text(html: str) -> str:
    """공시 원문 HTML에서 본문 텍스트만 남긴다. **표 구조를 파싱하지 않는다.**

    종류마다 표가 달라 파서를 종류 수만큼 두게 되는데, 모델에게 줄 것은 숫자 칸이 아니라
    문장이라 태그만 걷어내면 충분하다(2026-08-29 실측: 파생상품거래손실발생이 손실금액·
    자기자본대비·발생원인을 921자 안에 다 담는다).

    **`<style>`을 먼저 걷어낸다.** 태그만 벗기면 거래소 공시의 CSS가 본문 앞에 붙는다.

    **자르지 않는다**(2026-08-29 정정). 전에 4,000자 상한을 뒀는데 대량보유보고서 세 건이
    거기 걸려 저장 자체가 잘렸다. 저장은 원본 보존이고 **프롬프트에 얼마를 실을지는 읽는
    쪽이 정한다**(`causal.generation.MAX_DISCLOSURE_BODY_CHARS`). 한 행이 거대해지는 것은
    화이트리스트가 막는다 — 방대한 것은 정기보고서이고 그것이 목록에 없다.
    """
    text = SCRIPT_OR_STYLE_PATTERN.sub(" ", html)
    text = HTML_TAG_PATTERN.sub(" ", text)
    text = HTML_ENTITY_PATTERN.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def pending_bodies(
    connection: Connection,
    stock_codes: tuple[str, ...],
    since: date,
) -> tuple[Disclosure, ...]:
    """본문을 아직 못 받은 시장 반응형 공시. 이 조회가 재시도 목록이다."""
    with connection.cursor() as cursor:
        cursor.execute(
            DISCLOSURE_BODY_PENDING_SELECT,
            {
                "stock_codes": list(stock_codes),
                "since": since,
                "patterns": [f"%{keyword}%" for keyword in MATERIAL_REPORT_KEYWORDS],
                "limit": MAX_BODIES_PER_RUN,
            },
        )
        rows = cursor.fetchall() or []
    return tuple(
        Disclosure(
            corp_code=row[0],
            corp_name=row[1],
            stock_code=row[2],
            corp_class=row[3],
            report_name=row[4],
            rcept_no=row[5],
            filer_name=row[6],
            receipt_date=row[7],
            remarks=row[8],
        )
        for row in rows
        # SQL의 `LIKE ANY`가 정기보고서까지 걸 수 있어 파이썬에서 한 번 더 본다.
        if is_material(row[4])
    )


def normalized_report_name(report_name: str) -> str:
    """`[기재정정]` 같은 접두사를 뗀 이름. 원문 이름은 저장할 때 그대로 쓴다."""
    return CORRECTION_PREFIX_PATTERN.sub("", report_name).strip()


def is_provisional(report_name: str) -> bool:
    return normalized_report_name(report_name) == PROVISIONAL_REPORT_NAME


def periodic_report(report_name: str) -> tuple[int, str, date] | None:
    """정기보고서면 (사업연도, 보고서코드, 기간 종료일). 아니면 `None`.

    `분기보고서 (2026.03)` 꼴이다. 1분기와 3분기가 같은 이름이라 월로 가른다.
    """
    match = PERIODIC_REPORT_PATTERN.match(normalized_report_name(report_name))
    if match is None:
        return None
    year, month = int(match.group(2)), int(match.group(3))
    code = PERIODIC_REPORTS.get(month)
    if code is None:
        return None
    return year, code, date(year, month, QUARTER_END_DAYS[month])


def _table_cells(document: Selector, table_id: str) -> list[str]:
    tables = document.css(f"table#{table_id}")
    if not tables:
        raise DartPayloadError(f"provisional document has no table #{table_id}")
    return [cell.get_all_text(strip=True) for cell in tables[0].css("td")]


def _unit_multiplier(cells: list[str]) -> Decimal:
    """표가 밝힌 단위. `단위 : 억원, %`처럼 온다."""
    for cell in cells:
        if not cell.startswith("단위"):
            continue
        # **긴 이름부터 본다.** `조원`·`억원`이 모두 `원`을 품고 있어 짧은 것을 먼저 맞추면
        # 1조가 1원이 된다.
        for name in sorted(UNIT_MULTIPLIERS, key=len, reverse=True):
            if name in cell:
                return UNIT_MULTIPLIERS[name]
        raise DartPayloadError(f"provisional document has an unknown unit: {cell!r}")
    raise DartPayloadError("provisional document has no unit cell")


def _period_ends(cells: list[str]) -> dict[str, date]:
    """실적기간 표에서 기간 기준별 종료일을 읽는다."""
    labels = {"당기실적": "period", "당기누계실적": "cumulative"}
    ends: dict[str, date] = {}
    for index, cell in enumerate(cells):
        basis = labels.get(cell)
        if basis is None or index + 3 >= len(cells):
            continue
        try:
            ends[basis] = date.fromisoformat(cells[index + 3])
        except ValueError:
            raise DartPayloadError(f"provisional period is not a date: {cells[index + 3]!r}") from None
    missing = set(labels.values()) - set(ends)
    if missing:
        raise DartPayloadError(f"provisional document has no period for {sorted(missing)}")
    return ends


def parse_provisional(xml: str) -> tuple[tuple[EarningsValue, ...], ProvisionalMeta]:
    """잠정실적 원문에서 세 지표를 읽는다.

    표를 **id로 집는다.** 정정 공시에는 정정 요약 표(`XFormD8_*`)가 앞에 붙어 있어서
    위치로 찾으면 조용히 다른 숫자를 읽는다.
    """
    document = Selector(content=xml)
    result_cells = _table_cells(document, RESULT_TABLE_ID)
    multiplier = _unit_multiplier(result_cells)
    period_ends = _period_ends(_table_cells(document, PERIOD_TABLE_ID))

    scope_cell = next((cell for cell in result_cells if "실적내용" in cell), "")
    # `1. 연결실적내용` / `2. 별도실적내용`. 연결이 없으면 별도다.
    scope = "OFS" if "별도" in scope_cell else "CFS"

    values: list[EarningsValue] = []
    metric_name = ""
    for index, cell in enumerate(result_cells):
        if cell in PROVISIONAL_METRICS:
            metric_name = cell
            continue
        basis = BASIS_LABELS.get(cell)
        if basis is None or not metric_name:
            continue

        window = result_cells[index + 1 : index + 1 + PROVISIONAL_VALUE_COLUMNS]
        if len(window) < PROVISIONAL_VALUE_COLUMNS:
            raise DartPayloadError(f"provisional row {metric_name}/{cell} has {len(window)} value cells")

        label = metric_name
        if basis == "cumulative":
            # 지표 하나가 당해실적·누계실적 두 줄을 쓰고 끝난다. 여기서 지표를 놓지 않으면
            # 바로 아래 `지배기업 소유주지분 순이익` 행이 당기순이익으로 이어 읽힌다.
            metric_name = ""

        current = _amount(window[CURRENT_VALUE_INDEX], multiplier)
        if current is None:
            # 공시에 없는 값이다. 0으로 바꾸지 않고 행을 만들지 않는다.
            continue
        values.append(
            EarningsValue(
                metric=PROVISIONAL_METRICS[label],
                amount_basis=basis,
                statement_scope=scope,
                period_end=period_ends[basis],
                current_amount=current,
                prior_year_amount=_amount(window[PRIOR_YEAR_VALUE_INDEX], multiplier),
                currency="KRW",
                source_account_id=None,
                source_account_name=label,
            )
        )

    if not values:
        raise DartPayloadError("provisional document produced no metrics")

    return tuple(values), ProvisionalMeta(unit_multiplier=str(multiplier), statement_scope=scope)


def parse_financials(payload: dict[str, Any], period_end: date, scope: str) -> tuple[EarningsValue, ...]:
    """재무제표 응답에서 세 지표를 읽는다. 계정은 이름이 아니라 `account_id`로 잡는다."""
    values: list[EarningsValue] = []
    for row in payload.get("list") or []:
        if row.get("sj_div") != "IS":
            # 포괄손익계산서(CIS)에도 순이익이 중복으로 온다. 손익계산서만 읽는다.
            continue
        metric = FINANCIAL_ACCOUNTS.get(str(row.get("account_id", "")).strip())
        if metric is None:
            continue

        currency = str(row.get("currency", "")).strip() or "KRW"
        account_name = str(row.get("account_nm", "")).strip()
        for basis, current_key, prior_key in (
            ("period", "thstrm_amount", "frmtrm_amount"),
            ("cumulative", "thstrm_add_amount", "frmtrm_add_amount"),
        ):
            current = _amount(row.get(current_key) or "")
            if current is None:
                continue
            values.append(
                EarningsValue(
                    metric=metric,
                    amount_basis=basis,
                    statement_scope=scope,
                    period_end=period_end,
                    current_amount=current,
                    prior_year_amount=_amount(row.get(prior_key) or ""),
                    currency=currency,
                    source_account_id=str(row["account_id"]).strip(),
                    source_account_name=account_name,
                )
            )
    return tuple(values)


def _paging_field(payload: dict[str, Any], key: str, entity: FilingEntity) -> int:
    """페이지 계산에 쓰는 칸을 정수로 읽는다. 없거나 숫자가 아니면 실패다."""
    value = payload.get(key)
    if value is None:
        raise DartPayloadError(f"DART list.json for {entity.stock_code} has no {key}; the truncation check cannot run")
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise DartPayloadError(f"DART list.json for {entity.stock_code} has a non-numeric {key}: {value!r}") from error


def entity_of(entities: tuple[FilingEntity, ...], stock_code: str) -> FilingEntity:
    """공시가 속한 대상. 목록 밖의 종목이면 실패다.

    회사 번호로 조회하는 API가 있어(`fnlttSinglAcntAll`) 공시에서 그 번호를 되찾아야 한다.
    """
    for entity in entities:
        if entity.stock_code == stock_code:
            return entity
    raise DartPayloadError(f"disclosure belongs to an unexpected company: {stock_code}")


def _insert_source_record(
    cursor: Cursor,
    source_key: str,
    started_at: datetime,
    completed_at: datetime,
    status: str,
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
            status,
            record_count,
            # 원본은 남기지 않는다. API 키가 URL에 있으므로 요청도 남기지 않는다.
            None,
            json.dumps(metadata, ensure_ascii=False),
        ),
    )
    return cursor.fetchone()[0]


def pending_earnings(connection: Connection, stock_codes: tuple[str, ...], since: date) -> tuple[Disclosure, ...]:
    """실적 숫자를 아직 못 얻은 공시. 별도 작업 큐 없이 이 조회가 재시도 목록이다."""
    with connection.cursor() as cursor:
        cursor.execute(DISCLOSURE_EVENT_PENDING_SELECT, (list(stock_codes), since))
        rows = cursor.fetchall() or []
    return tuple(
        Disclosure(
            corp_code=row[0],
            corp_name=row[1],
            stock_code=row[2],
            corp_class=row[3],
            report_name=row[4],
            rcept_no=row[5],
            filer_name=row[6],
            receipt_date=row[7],
            remarks=row[8],
        )
        for row in rows
    )


def recent_report_periods(today: date, count: int = MULTI_ACCOUNT_PERIODS) -> tuple[tuple[int, str], ...]:
    """`today` 기준으로 이미 끝난 최근 분기 `count`개의 `(사업연도, 보고서코드)`.

    **기준 날짜는 KST다.** 부르는 쪽(DAG)이 `data_interval_end`를 KST로 바꿔 넘긴다.

    분기가 끝났다는 것과 보고서가 올라왔다는 것은 다르다 — 정기보고서는 기간 종료 뒤
    45일(사업보고서는 90일)까지 제출한다. 그래서 **가장 최근 기간이 아직 0건인 것은 정상이고,
    넷이 전부 0건인 것만 실패다.** 그 판정은 DAG가 한다.
    """
    if count < 1:
        raise ValueError("count must be positive")
    quarters = [(year, month) for year in (today.year, today.year - 1) for month, _ in QUARTER_REPORT_CODES]
    ended = sorted(
        (year, month) for year, month in quarters if date(year, month, QUARTER_END_DAYS[month]) < today
    )
    if len(ended) < count:
        raise ValueError(f"cannot look back {count} quarters from {today.isoformat()}")
    codes = dict(QUARTER_REPORT_CODES)
    return tuple((year, codes[month]) for year, month in ended[-count:])


def _multi_account_period_end(text: str) -> date:
    """`2026.01.01 ~ 2026.03.31`의 끝 날짜.

    **모양을 먼저 본다.** 재무상태표 행은 `2026.03.31 현재`로 오고, 그것이 여기 들어오면
    구간 없는 값을 기간 종료일로 읽는다. 손익계산서만 읽으므로 오지 않아야 하고, 와도
    실패시킨다.
    """
    match = MULTI_ACCOUNT_PERIOD_PATTERN.search(str(text))
    if match is None:
        raise DartPayloadError(f"multi-account period is not a date range: {text!r}")
    year, month, day = (int(part) for part in match.group(4, 5, 6))
    try:
        return date(year, month, day)
    except ValueError:
        raise DartPayloadError(f"multi-account period end is not a real date: {text!r}") from None


def parse_multi_accounts(
    payload: dict[str, Any],
    entities: tuple[FilingEntity, ...],
) -> tuple[MultiAccountEntry, ...]:
    """다중회사 주요계정 응답을 회사·재무제표 범위마다 갈라 읽는다.

    **요청하지 않은 회사가 섞여 오면 실패시킨다.** 콤마로 이어 보내는 요청이라 번호 하나가
    잘못 붙으면 남의 회사 실적이 우리 종목코드로 저장될 수 있다.

    **손익계산서(`IS`)만 읽는다.** 포괄손익계산서(`CIS`)에도 순이익이 중복으로 온다.
    """
    by_entity_id = {entity.filing_entity_id: entity for entity in entities}
    grouped: dict[tuple[str, str], dict[tuple[str, str], EarningsValue]] = {}
    receipts: dict[tuple[str, str], str] = {}

    for row in payload.get("list") or []:
        if str(row.get("sj_div", "")).strip() != "IS":
            continue
        entity_id = str(row.get("corp_code", "")).strip()
        entity = by_entity_id.get(entity_id)
        if entity is None:
            raise DartPayloadError(f"DART answered with corp_code {entity_id!r} which was not requested")

        account_name = str(row.get("account_nm", "")).strip()
        metric = MULTI_ACCOUNT_METRICS.get(account_name)
        if metric is None:
            continue

        scope = str(row.get("fs_div", "")).strip()
        if scope not in (StatementScope.CFS, StatementScope.OFS):
            raise DartPayloadError(f"unknown fs_div {scope!r} for {entity.stock_code}")

        rcept_no = str(row.get("rcept_no", "")).strip()
        if not rcept_no:
            raise DartPayloadError(f"multi-account row for {entity.stock_code} has no rcept_no")
        key = (entity.stock_code, scope)
        receipts.setdefault(key, rcept_no)

        period_end = _multi_account_period_end(row.get("thstrm_dt", ""))
        currency = str(row.get("currency", "")).strip() or "KRW"
        for basis, current_key, prior_key in MULTI_ACCOUNT_BASES:
            current = _amount(row.get(current_key) or "")
            if current is None:
                continue
            value = EarningsValue(
                metric=metric,
                amount_basis=basis,
                statement_scope=scope,
                period_end=period_end,
                current_amount=current,
                prior_year_amount=_amount(row.get(prior_key) or ""),
                currency=currency,
                # 이 API는 `account_id`를 주지 않는다. 되짚을 근거는 계정명뿐이다.
                source_account_id=None,
                source_account_name=account_name,
            )
            values = grouped.setdefault(key, {})
            existing = values.get((metric, basis))
            if existing is None:
                values[(metric, basis)] = value
                continue
            # `당기순이익(손실)`은 손익계산서 안에서 두 번 온다(전 회사 실측 2026-09-04).
            # 값이 같은 중복이라 하나만 남기고, **다르면 어느 줄이 맞는지 고를 수 없으므로
            # 실패시킨다.** 조용히 뒤엣것으로 덮으면 되짚을 수 없다.
            if existing.current_amount != value.current_amount or existing.prior_year_amount != value.prior_year_amount:
                raise DartPayloadError(
                    f"multi-account rows for {entity.stock_code} {scope} {metric}/{basis} disagree: "
                    f"{existing.current_amount} vs {value.current_amount}"
                )

    return tuple(
        MultiAccountEntry(
            stock_code=stock_code,
            rcept_no=receipts[(stock_code, scope)],
            statement_scope=scope,
            values=tuple(values.values()),
        )
        for (stock_code, scope), values in sorted(grouped.items())
        if values
    )


class DartCollector:
    """OpenDART 수집기. API 키를 쥐고 공시 목록·원문·재무제표를 조회하고 저장한다.

    한 실행이 객체 하나다. 키는 이 객체가 사는 동안 안 변하는 값이라 생성자가 받고, 회사·구간·
    공시처럼 호출마다 바뀌는 것은 메서드 인자다. 전송(`_get`)과 파싱(`parse_provisional`·
    `parse_financials`), 그리고 DART와 무관하게 DB만 보는 `pending_earnings`는 밖에 둔다.
    """

    def __init__(self, api_key: SecretStr) -> None:
        if not api_key.get_secret_value():
            raise ValueError("DART API key is required")
        self._api_key = api_key

    def _call(self, path: str, params: dict[str, str]) -> bytes:
        """키를 붙여 한 번 부른다. **키가 질의 문자열에 들어가므로 URL을 예외나 로그에 남기지 않는다.**"""
        return _get(f"{OPENDART_BASE_URL}/{path}", {"crtfc_key": self._api_key.get_secret_value(), **params})

    def fetch_disclosures(
        self,
        entity: FilingEntity,
        begin_date: date,
        end_date: date,
    ) -> DisclosureFetch:
        """한 회사의 공시 목록을 받는다. 데이터 없음은 0건 성공이다."""
        started_at = datetime.now(UTC)
        rows: list[dict[str, Any]] = []
        total_count = 0
        page = 0

        while page < MAX_PAGES:
            page += 1
            payload = _json(
                self._call(
                    "list.json",
                    {
                        "corp_code": entity.filing_entity_id,
                        "bgn_de": begin_date.strftime("%Y%m%d"),
                        "end_de": end_date.strftime("%Y%m%d"),
                        "sort": "date",
                        "sort_mth": "desc",
                        "last_reprt_at": "N",
                        "page_no": str(page),
                        "page_count": str(PAGE_COUNT),
                    },
                )
            )
            status = str(payload.get("status", ""))
            if status == STATUS_NO_DATA:
                break
            if status != STATUS_OK:
                raise DartStatusError(status, str(payload.get("message", "")).strip())

            rows.extend(payload.get("list") or [])
            # 아래 잘림 검사가 이 두 칸 위에 서 있다. 없는 값을 0·1로 메우면 검사가 조용히
            # 무력화되므로(0이면 늘 통과, 1이면 첫 장에서 끝) 값 자체를 요구한다.
            total_count = _paging_field(payload, "total_count", entity)
            if page >= _paging_field(payload, "total_page", entity):
                break

        # 장 상한에서 멈추면 조회 구간에 구멍이 남는다. 조용히 잘린 목록보다 실패가 낫다.
        if total_count > len(rows):
            raise DartPayloadError(
                f"DART returned {total_count} disclosures for {entity.stock_code} "
                f"but only {len(rows)} were read in {page} pages; narrow the window or raise MAX_PAGES"
            )

        try:
            disclosures = tuple(Disclosure.from_payload(row) for row in rows)
        except (KeyError, TypeError) as error:
            raise DartPayloadError(f"DART disclosure row is malformed: {error}") from None

        return DisclosureFetch(
            company=entity.stock_code,
            corp_code=entity.filing_entity_id,
            begin_date=begin_date,
            end_date=end_date,
            disclosures=disclosures,
            page_count=page,
            total_count=total_count,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def fetch_provisional(self, disclosure: Disclosure) -> EarningsFetch | None:
        """잠정실적 공시 원문을 받아 세 지표를 뽑는다. 원문이 아직 없으면 `None`이다."""
        started_at = datetime.now(UTC)
        raw = self._call("document.xml", {"rcept_no": disclosure.rcept_no})

        # ZIP이 아니면 오류 XML이다. HTTP는 200으로 온다.
        if raw[:2] != b"PK":
            text = raw.decode("utf-8", errors="replace")
            code = re.search(r"<status>(\d+)</status>", text)
            status = code.group(1) if code else ""
            if status == STATUS_NO_FILE:
                logger.info("Document for %s is not available yet", disclosure.rcept_no)
                return None
            raise DartStatusError(status, "document.xml did not return a ZIP")

        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if not names:
                raise DartPayloadError(f"document ZIP for {disclosure.rcept_no} is empty")
            content = archive.read(names[0])

        values, metadata = parse_provisional(content.decode("utf-8", errors="replace"))
        return EarningsFetch(
            rcept_no=disclosure.rcept_no,
            stock_code=disclosure.stock_code,
            release_type="provisional",
            values=values,
            metadata={
                "rcept_no": disclosure.rcept_no,
                "file_name": names[0],
                # 같은 접수번호의 첨부가 바뀌면 이 해시가 달라진다.
                "sha256": hashlib.sha256(raw).hexdigest(),
                **metadata.model_dump(mode="json"),
            },
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def fetch_body(self, disclosure: Disclosure) -> str | None:
        """공시 원문 본문. 원문이 아직 안 올라왔으면 `None`이다.

        `fetch_provisional`과 같은 `document.xml`을 부르지만 **여기서는 파싱하지 않는다** —
        저쪽은 표에서 숫자를 뽑고 이쪽은 문장을 그대로 준다. 종류가 열 가지라 표 파서를
        그만큼 둘 수 없고, 모델에게 줄 것도 숫자 칸이 아니라 문장이다.
        """
        raw = self._call("document.xml", {"rcept_no": disclosure.rcept_no})

        # ZIP이 아니면 오류 XML이다. HTTP는 200으로 온다(`fetch_provisional`과 같은 계약).
        if raw[:2] != b"PK":
            text = raw.decode("utf-8", errors="replace")
            code = re.search(r"<status>(\d+)</status>", text)
            status = code.group(1) if code else ""
            if status == STATUS_NO_FILE:
                logger.info("Document for %s is not available yet", disclosure.rcept_no)
                return None
            raise DartStatusError(status, "document.xml did not return a ZIP")

        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if not names:
                raise DartPayloadError(f"document ZIP for {disclosure.rcept_no} is empty")
            content = archive.read(names[0])

        body = disclosure_text(content.decode("utf-8", errors="replace"))
        if not body:
            # 태그를 걷어냈더니 아무 것도 안 남았다. 원문 형식이 바뀐 것이라 조용히
            # 빈 문자열을 저장하면 다음 폴링이 그 공시를 다시 안 본다.
            raise DartPayloadError(f"document for {disclosure.rcept_no} has no text")
        return body

    def fetch_financials(self, disclosure: Disclosure, entities: tuple[FilingEntity, ...]) -> EarningsFetch | None:
        """정기보고서의 재무제표를 받는다.

        이 API는 접수번호가 아니라 회사·사업연도·보고서코드로 조회한다. **응답 `rcept_no`가
        처리 중인 공시와 다르면 저장하지 않는다.** 아직 반영 전이라는 뜻이고 다음 run이 다시 본다.

        연결(`CFS`)을 먼저 보고 없을 때만 별도(`OFS`)를 쓴다. 둘을 합치거나 대체하지 않는다.
        """
        periodic = periodic_report(disclosure.report_name)
        if periodic is None:
            return None
        year, report_code, period_end = periodic

        started_at = datetime.now(UTC)
        # 응답도 왔고 이 공시의 행도 맞는데 계정 매핑이 하나도 못 집은 범위를 모은다.
        # 아직 안 올라온 것(`STATUS_NO_DATA`)과 우리 파서가 못 읽은 것이 구분돼야 한다.
        unparsed: list[str] = []
        for scope in ("CFS", "OFS"):
            payload = _json(
                self._call(
                    "fnlttSinglAcntAll.json",
                    {
                        "corp_code": entity_of(entities, disclosure.stock_code).filing_entity_id,
                        "bsns_year": str(year),
                        "reprt_code": report_code,
                        "fs_div": scope,
                    },
                )
            )
            status = str(payload.get("status", ""))
            if status == STATUS_NO_DATA:
                continue
            if status != STATUS_OK:
                raise DartStatusError(status, str(payload.get("message", "")).strip())

            rows = payload.get("list") or []
            answered = {str(row.get("rcept_no", "")).strip() for row in rows}
            if disclosure.rcept_no not in answered:
                logger.info(
                    "Financial statements for %s still answer with %s; retrying next run",
                    disclosure.rcept_no,
                    sorted(answered)[:1],
                )
                return None

            values = parse_financials(payload, period_end, scope)
            if not values:
                unparsed.append(f"{scope}({len(rows)} rows)")
                continue
            return EarningsFetch(
                rcept_no=disclosure.rcept_no,
                stock_code=disclosure.stock_code,
                release_type="periodic",
                values=values,
                metadata={
                    "rcept_no": disclosure.rcept_no,
                    "business_year": year,
                    "report_code": report_code,
                    "statement_scope": scope,
                    "row_count": len(rows),
                    # 재무제표 금액은 원 단위로 온다. 곱하지 않는다.
                    "unit_multiplier": "1",
                },
                started_at=started_at,
                completed_at=datetime.now(UTC),
            )

        if unparsed:
            # 원문 계정 ID가 바뀐 것이다. 그대로 `None`을 돌려주면 "아직 안 올라왔다"와
            # 같아 보여 매 실행이 같은 공시를 조용히 건너뛴다.
            raise DartPayloadError(
                f"DART financial statements for {disclosure.rcept_no} answered with rows but no known accounts "
                f"were parsed: {', '.join(unparsed)}"
            )
        return None

    def fetch_multi_accounts(
        self,
        entities: tuple[FilingEntity, ...],
        business_year: int,
        report_code: str,
    ) -> MultiAccountFetch:
        """대상 회사 전체의 주요계정을 **한 번에** 받는다. 아직 안 올라왔으면 0건 성공이다.

        `fnlttSinglAcntAll`과 달리 회사 번호를 콤마로 이어 보내므로 스무 곳이 호출 하나다.
        대신 응답이 `account_id`를 주지 않아 계정을 이름으로 잡는다(`MULTI_ACCOUNT_METRICS`).
        """
        if not entities:
            raise ValueError("multi-account fetch needs at least one filing entity")

        started_at = datetime.now(UTC)
        payload = _json(
            self._call(
                "fnlttMultiAcnt.json",
                {
                    "corp_code": ",".join(entity.filing_entity_id for entity in entities),
                    "bsns_year": str(business_year),
                    "reprt_code": report_code,
                },
            )
        )
        status = str(payload.get("status", ""))
        rows: list[dict[str, Any]] = []
        if status == STATUS_NO_DATA:
            # 기간은 끝났는데 보고서가 아직 안 올라왔다. 실패가 아니고 다음 실행이 다시 본다.
            logger.info("Multi-account %s/%s is not available yet", business_year, report_code)
        elif status != STATUS_OK:
            raise DartStatusError(status, str(payload.get("message", "")).strip())
        else:
            rows = payload.get("list") or []

        entries = parse_multi_accounts({"list": rows}, entities)
        if rows and not entries:
            # 응답은 왔는데 아는 계정이 하나도 없다. 계정명이 바뀐 것이라 0건으로 두면
            # 매 실행이 같은 자리에서 조용히 아무 것도 안 남긴다.
            raise DartPayloadError(
                f"DART multi-account {business_year}/{report_code} answered with {len(rows)} rows "
                f"but no known accounts were parsed"
            )

        return MultiAccountFetch(
            business_year=business_year,
            report_code=report_code,
            entries=entries,
            requested_count=len(entities),
            answered_count=len({entry.stock_code for entry in entries}),
            row_count=len(rows),
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    def store_multi_accounts(self, connection: Connection, fetch: MultiAccountFetch) -> int:
        """다중회사 주요계정을 저장하고 저장한 행 수를 돌려준다.

        **조회 하나가 `source_record` 하나다.** 회사마다 계보를 만들면 응답 한 번이 스무 행을
        남긴다. 0건이어도 남긴다 — 조회했지만 아직 없는 기간과 아직 조회하지 않은 기간이
        구분돼야 한다.
        """
        with connection.cursor() as cursor:
            source_record_id = _insert_source_record(
                cursor,
                SOURCE_KEY_MULTI_ACCOUNT,
                fetch.started_at,
                fetch.completed_at,
                "succeeded",
                fetch.value_count,
                {
                    "business_year": fetch.business_year,
                    "report_code": fetch.report_code,
                    "requested_count": fetch.requested_count,
                    "answered_count": fetch.answered_count,
                    "row_count": fetch.row_count,
                    # 주요계정 금액은 원 단위로 온다. 곱하지 않는다.
                    "unit_multiplier": "1",
                },
            )
            execute_upserts(
                cursor,
                EARNINGS_FACT_UPSERT,
                [
                    (
                        entry.stock_code,
                        entry.rcept_no,
                        "periodic",
                        value.period_end,
                        value.statement_scope,
                        value.amount_basis,
                        value.metric,
                        value.current_amount,
                        value.prior_year_amount,
                        value.currency,
                        value.source_account_id,
                        value.source_account_name,
                        source_record_id,
                    )
                    for entry in fetch.entries
                    for value in entry.values
                ],
            )
        return fetch.value_count

    def store_disclosures(
        self, connection: Connection, fetch: DisclosureFetch, detected_at: datetime | None = None
    ) -> int:
        """공시 이벤트를 저장하고 저장한 건수를 돌려준다.

        `detected_at`은 새 행에만 들어간다. 이미 있는 접수번호는 최초값을 지킨다.
        """
        detected_at = detected_at or fetch.completed_at
        with connection.cursor() as cursor:
            source_record_id = _insert_source_record(
                cursor,
                SOURCE_KEY_LIST,
                fetch.started_at,
                fetch.completed_at,
                "succeeded",
                len(fetch.disclosures),
                {
                    "company": fetch.company,
                    "corp_code": fetch.corp_code,
                    "begin_date": fetch.begin_date.isoformat(),
                    "end_date": fetch.end_date.isoformat(),
                    "page_count": fetch.page_count,
                    "total_count": fetch.total_count,
                },
            )
            execute_upserts(
                cursor,
                DISCLOSURE_EVENT_UPSERT,
                [
                    (
                        disclosure.corp_code,
                        disclosure.stock_code,
                        disclosure.corp_name,
                        disclosure.rcept_no,
                        disclosure.report_name,
                        disclosure.filer_name,
                        disclosure.corp_class,
                        disclosure.receipt_date,
                        detected_at,
                        disclosure.remarks,
                        source_record_id,
                    )
                    for disclosure in fetch.disclosures
                ],
            )
        return len(fetch.disclosures)

    @staticmethod
    def store_body(connection: Connection, rcept_no: str, body: str) -> int:
        """본문을 채운다. 이미 채워져 있으면 0이다(`update_body.sql`의 `body IS NULL`)."""
        with connection.cursor() as cursor:
            cursor.execute(DISCLOSURE_BODY_UPDATE, {"body": body, "rcept_no": rcept_no})
            return cursor.rowcount or 0

    def store_earnings(self, connection: Connection, fetch: EarningsFetch) -> int:
        """실적 지표를 저장하고 저장한 행 수를 돌려준다."""
        source_key = SOURCE_KEY_DOCUMENT if fetch.release_type == "provisional" else SOURCE_KEY_FINANCIALS
        with connection.cursor() as cursor:
            source_record_id = _insert_source_record(
                cursor,
                source_key,
                fetch.started_at,
                fetch.completed_at,
                "succeeded",
                len(fetch.values),
                fetch.metadata,
            )
            execute_upserts(
                cursor,
                EARNINGS_FACT_UPSERT,
                [
                    (
                        fetch.stock_code,
                        fetch.rcept_no,
                        fetch.release_type,
                        value.period_end,
                        value.statement_scope,
                        value.amount_basis,
                        value.metric,
                        value.current_amount,
                        value.prior_year_amount,
                        value.currency,
                        value.source_account_id,
                        value.source_account_name,
                        source_record_id,
                    )
                    for value in fetch.values
                ],
            )
        return len(fetch.values)
