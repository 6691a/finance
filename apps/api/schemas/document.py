"""수집한 문서와 공시의 응답 계약.

**원문과 LLM 평가를 한 응답에 담는다**(사용자 결정 2026-08-27). 따로 보면 "이 기사가 왜
근거로 뽑혔나"를 두 화면에서 맞춰 봐야 한다.

**상태 머신이 없다.** 소비자가 사람이 아니라 LLM이라 전부 저장하고 점수만 남기는 것이
`document` 테이블의 설계이고, 화면도 그것을 그대로 보인다 — 승인·보류 같은 칸이 없다.
"""

from datetime import date
from typing import Any

from pydantic import Field

from apps.api.schemas.common import ApiModel, Page, UtcDatetime


class DocumentSourceItem(ApiModel):
    """문서 출처 하나. **`enabled`가 카테고리를 통째로 끄는 손잡이다.**"""

    slug: str = Field(description="출처 식별자. `document.source_slug`와 같은 값이다.")
    name: str = Field(description="표시 이름.")
    source_kind: str = Field(description="출처 종류(news·research·government 등).")
    country: str | None = Field(default=None, description="발행 국가.")
    language: str | None = Field(default=None, description="본문 언어.")
    collection_mode: str = Field(description="수집 방식(feed·listing 등).")
    enabled: bool = Field(description="지금 수집이 도는가. **끄는 것이 이용조건 문제의 해결 경로다.**")
    terms_url: str | None = Field(default=None, description="이용조건 URL. 결정을 남겨 둔 출처만 있다.")
    terms_checked_at: UtcDatetime | None = Field(default=None, description="이용조건을 확인한 시각.")
    documents: int = Field(default=0, description="이 출처로 들어온 문서 수.")
    latest_at: UtcDatetime | None = Field(default=None, description="가장 최근 문서의 발행 시각. 최신성이다.")


DocumentSourceList = Page[DocumentSourceItem]


class DocumentSummary(ApiModel):
    """문서 목록 한 줄. **본문은 없다** — 3천 건이면 응답이 수십 MB가 된다."""

    id: int = Field(description="문서 id.")
    source_slug: str = Field(description="출처 식별자.")
    external_id: str = Field(description="출처 안의 고유 id. `(source_slug, external_id)`가 자연키다.")
    title: str = Field(description="제목.")
    document_type: str = Field(description="문서 종류(news·report·release 등).")
    published_at: UtcDatetime = Field(description="발행 시각(UTC).")
    language: str | None = Field(default=None, description="본문 언어.")
    body_status: str | None = Field(
        default=None,
        description=(
            "본문을 받아 봤는가, 못 받았다면 왜인가(ok·empty·attachment_only·unavailable). "
            "**null은 아직 해 보지 않았다는 뜻이고 그 집합이 곧 수집 큐다** — 실패를 상태로 "
            "남기지 않아서 연결 오류와 5xx는 여기 없다."
        ),
    )
    canonical_url: str | None = Field(default=None, description="원문 URL.")
    value_score: int | None = Field(
        default=None,
        description=(
            "LLM이 매긴 값어치. **평가 전이면 null이고 0이 아니다** — 평가에 실패한 문서는 "
            "`assessed_at`이 null로 남고 다음 정시 실행이 다시 집는다."
        ),
    )
    direction: str | None = Field(default=None, description="LLM이 본 방향(up/down/flat).")
    assessed_at: UtcDatetime | None = Field(default=None, description="평가한 시각. null이면 미평가다.")
    llm_model: str | None = Field(default=None, description="평가를 만든 모델.")
    prompt_version: str | None = Field(default=None, description="평가 프롬프트 판.")
    instruments: tuple[str, ...] = Field(default=(), description="이 문서에 붙은 종목 태그.")
    indicators: tuple[str, ...] = Field(default=(), description="이 문서에 붙은 지표 시계열 태그.")


DocumentList = Page[DocumentSummary]


class DocumentAttachmentItem(ApiModel):
    """문서에 붙은 첨부 하나.

    **본문이 첨부에만 있는 출처가 있다**(한국은행·금감원·BOJ·네이버 리서치). 그때
    `body_status`가 `attachment_only`이고, 그 문서에서 읽을 것은 이 목록이 전부다.
    """

    position: int = Field(description="문서 안에서의 순서(0부터). 페이지에 나온 차례다.")
    kind: str = Field(description="내려받은 파일(file)인지 링크만 남긴 영상(video)인지.")
    url: str = Field(description="첨부 원본 URL. **영상은 이 값이 전부다.**")
    filename: str | None = Field(default=None, description="제공처가 준 파일 이름.")
    media_type: str | None = Field(default=None, description="응답의 Content-Type(application/pdf 등).")
    byte_size: int | None = Field(default=None, description="받은 파일의 바이트 수. 영상은 null이다.")
    stored: bool = Field(
        default=False,
        description=(
            "우리가 파일을 받아 뒀나. **경로 자체는 내지 않는다** — 마운트 안의 상대경로라 "
            "화면에서 열 수 있는 주소가 아니고, 그 자리를 밖으로 알릴 이유도 없다."
        ),
    )
    fetched_at: UtcDatetime | None = Field(default=None, description="파일을 내려받은 시각(UTC).")


class DocumentDetail(DocumentSummary):
    """문서 하나. 목록 한 줄에 본문·평가 전문과 첨부를 더한 것이다."""

    body: str | None = Field(
        default=None,
        description=(
            "본문 전문. 아직 못 받았으면 null이고 그 사유는 `body_status`가 갖는다. "
            "**HTML로 해석하지 않는다.**"
        ),
    )
    summary: str | None = Field(default=None, description="LLM이 쓴 요약.")
    assessment: dict[str, Any] | None = Field(
        default=None,
        description=(
            "LLM 응답 전체(세부 점수·주제·새 사실·판단 근거·근거 청크). **문자열이 아니라 "
            "jsonb다** — 조회 조건이 굳으면 컬럼으로 빠질 값이라 지금은 모양을 고정하지 않는다. "
            "화면은 이것을 원문 그대로 보인다."
        ),
    )
    detected_at: UtcDatetime = Field(description="우리가 이 문서를 처음 본 시각(UTC).")
    content_hash: str = Field(description="본문 해시. 바뀌면 같은 행을 갱신한다.")
    assessed_content_hash: str | None = Field(
        default=None,
        description="평가 당시의 본문 해시. 현재 `content_hash`와 다르면 재평가 대상이다.",
    )
    attachments: tuple[DocumentAttachmentItem, ...] = Field(
        default=(),
        description=(
            "이 문서의 첨부. **본문이 첨부에만 있는 출처가 있어**(`body_status`가 "
            "`attachment_only`) 그때는 이 목록이 문서의 내용 전부다."
        ),
    )


class DisclosureItem(ApiModel):
    """DART 공시 접수 하나. 접수번호가 단위다."""

    rcept_no: str = Field(description="접수번호. 이것이 자연키다.")
    stock_code: str | None = Field(default=None, description="6자리 종목코드. 비상장 법인은 null이다.")
    corp_code: str = Field(description="DART 법인 고유번호.")
    company_name: str = Field(description="법인 이름.")
    report_name: str = Field(description="보고서 이름.")
    filer_name: str | None = Field(default=None, description="제출인.")
    corp_class: str | None = Field(default=None, description="법인 구분(Y·K·N·E).")
    receipt_date: date = Field(description="접수일(KST).")
    detected_at: UtcDatetime = Field(description="우리가 감지한 시각(UTC).")
    remarks: str | None = Field(default=None, description="비고.")
    has_body: bool = Field(
        default=False,
        description=(
            "공시 본문을 받아 뒀나. **본문 자체는 목록에 싣지 않는다** — 한 건이 만 자를 넘고"
            "(2026-08-30 실측 최대 14,695자) 그건 목록의 일이 아니다. 원문은 `url`이 준다. "
            "false는 아직 못 받았거나 받을 대상이 아니라는 뜻이다 — 시장이 반응하는 종류만 채운다."
        ),
    )
    url: str | None = Field(
        default=None,
        description=(
            "DART 원문 뷰어 주소. **저장하는 값이 아니라 접수번호로 만든 것이다** — "
            "제공처가 `dart`가 아니면 null이다."
        ),
    )


DisclosureList = Page[DisclosureItem]


class EarningsFactItem(ApiModel):
    """공시에서 뽑은 실적 지표 하나. **이것이 실제값의 원본이다** — 기사 산문에서 다시 뽑지 않는다."""

    stock_code: str = Field(description="6자리 종목코드.")
    rcept_no: str = Field(description="근거 공시의 접수번호.")
    release_type: str = Field(description="공시 종류(잠정·확정).")
    period_end: date = Field(description="대상 기간의 끝.")
    statement_scope: str = Field(description="연결·별도 구분.")
    amount_basis: str = Field(description="누적·당기 구분.")
    metric: str = Field(
        description=(
            "지표(revenue·operating_profit·net_income). "
            "**`stock_event_claim.metric`과 글자 그대로 같다** — 판정이 대응표 없이 조인한다."
        )
    )
    current_amount: float | None = Field(default=None, description="당기 금액(원).")
    prior_year_amount: float | None = Field(default=None, description="전년 동기 금액(원).")
    currency: str = Field(description="통화. 지금은 전부 KRW다.")
    source_account_name: str | None = Field(default=None, description="원문 표의 계정 이름.")
    url: str | None = Field(
        default=None,
        description=(
            "이 숫자를 뽑은 공시의 DART 원문 뷰어 주소. 접수번호로 만든다 — "
            "**숫자만 보고 못 믿겠을 때 원문으로 가는 길이다.**"
        ),
    )


EarningsFactList = Page[EarningsFactItem]
