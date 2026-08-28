"""수집 원장과 마스터의 응답 계약.

**`source_record`가 "무엇이 언제까지 채워졌나"의 원본이다.** 관측값이 0건이어도 행이
남으므로, 조회했지만 값이 없는 구간과 아직 조회하지 않은 구간이 여기서 구분된다.

**`payload`를 목록에 싣지 않는다.** jsonb 원본이라 행 하나가 수백 KB일 수 있다.
"""

from datetime import date

from pydantic import Field

from apps.api.schemas.common import ApiModel, Page, UtcDatetime


class SourceHealth(ApiModel):
    """출처 하나의 최신성과 실패. **화면의 첫 줄이 이것이다.**"""

    source: str = Field(description="제공처 식별자(kis·yahoo·fred·dart 등).")
    source_type: str = Field(description="수집 방식(api·crawl·websocket).")
    records: int = Field(default=0, description="이 창에서 남은 수집 레코드 수.")
    succeeded: int = Field(default=0, description="성공한 수집 수.")
    failed: int = Field(default=0, description="실패한 수집 수.")
    running: int = Field(
        default=0,
        description=(
            "종료를 기록하지 못한 수집 수. **지금 도는 중인지 끊긴 것인지 이 값만으로 "
            "가르지 않는다** — heartbeat가 없다."
        ),
    )
    quarantined: int = Field(
        default=0,
        description="격리된 수집 수. 값이 의심스러워 따로 뺀 것이라 실패와 다르다.",
    )
    rows: int = Field(default=0, description="이 창에서 만들어진 정규화 행 수의 합.")
    latest_at: UtcDatetime | None = Field(default=None, description="가장 최근 수집의 시작 시각(UTC).")
    latest_status: str | None = Field(default=None, description="그 수집의 상태.")


class SourceHealthList(Page[SourceHealth]):
    """출처별 요약 한 쪽. **가장 최근 수집이 오래된 순이다** — 늦은 것이 위다."""

    since: UtcDatetime = Field(description="이 요약이 본 창의 시작(UTC).")


class SourceRecordRow(ApiModel):
    """수집 한 번. **`payload`는 없다** — 단위가 응답 1회·문서 1버전·배치 1개다."""

    id: int = Field(description="레코드 id.")
    source_type: str = Field(description="수집 방식.")
    source: str = Field(description="제공처 식별자.")
    source_key: str | None = Field(
        default=None,
        description="이 수집이 무엇을 집었나(시계열 id·파일 이름·조회 이름). 파일 단위 수집이면 파일 이름이다.",
    )
    started_at: UtcDatetime = Field(description="수집 시작(UTC).")
    completed_at: UtcDatetime | None = Field(default=None, description="수집 끝(UTC). null이면 종료를 못 남겼다.")
    status: str = Field(description="상태(succeeded·failed·running 등).")
    record_count: int | None = Field(
        default=None, description="이 수집이 만든 정규화 행 수. **0도 정상이다** — 값 없음과 미조회는 다르다."
    )
    has_payload: bool = Field(default=False, description="원본 JSON을 함께 보관했나. 본문은 목록에 싣지 않는다.")
    payload_uri: str | None = Field(default=None, description="대용량 원본의 외부 저장 위치.")


SourceRecordList = Page[SourceRecordRow]


class InstrumentRow(ApiModel):
    """추적 종목 마스터 하나. **관측값이 아니라 기준 정보다** — 수집 계보를 잇지 않는다."""

    ticker: str = Field(description="티커. `(ticker, market)`이 자연키다.")
    market: str = Field(description="시장(kospi·kosdaq 등).")
    name: str = Field(description="종목 이름.")
    kind: str = Field(description="종류(stock 등).")
    currency: str = Field(description="통화.")
    source_symbol: str | None = Field(
        default=None, description="수집 소스 심볼. 티커와 같으면 null이다."
    )
    is_watched: bool = Field(
        description="수집·분석 대상인가. **상장폐지 같은 생애주기 상태가 아니다** — 그건 생기면 별도 칸이 된다."
    )


InstrumentList = Page[InstrumentRow]


class MarketSessionRow(ApiModel):
    """시장·날짜별 개장 여부와 결제일."""

    market_code: str = Field(description="시장 코드.")
    market_name: str = Field(description="시장 이름.")
    country_code: str = Field(description="국가.")
    session_date: date = Field(description="날짜.")
    kis_business_day: bool | None = Field(default=None, description="제공처 기준 영업일인가.")
    kis_trading_day: bool | None = Field(default=None, description="거래일인가.")
    kis_open_day: bool | None = Field(default=None, description="개장일인가.")
    effective_open_day: bool | None = Field(
        default=None, description="우리가 쓰는 개장 판정. **검증이 붙으면 제공처 값과 갈릴 수 있다.**"
    )
    local_settlement_date: date | None = Field(default=None, description="현지 결제일.")
    domestic_settlement_date: date | None = Field(default=None, description="국내 결제일.")
    verified_by: str | None = Field(default=None, description="개장 여부를 무엇으로 확인했나.")


MarketSessionList = Page[MarketSessionRow]
