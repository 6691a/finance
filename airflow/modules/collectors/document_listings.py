"""피드가 없는 출처의 목록 페이지에서 경제 문서를 발견한다.

`documents.py`가 RSS·Atom을 읽는 것과 짝이다. KRX와 금감원은 피드를 제공하지 않아
게시판 목록을 직접 읽는다 — KRX는 JSON 서블릿, 금감원은 eGov 게시판 HTML이다.
결과 타입(`FeedItem`, `FeedResponse`)을 `documents.py`와 같게 유지해 저장은
`store_documents`를 그대로 쓴다. 출처별 특례를 slug 키 테이블로 두는 것도
`NAIVE_FEED_TIMEZONES`·`SERIES_GUID_SOURCES` 선례 그대로다.

**두 채널 모두 실제로 요청해 응답을 확인했다**(2026-08-20).

- KRX: `POST /contents/OPN/99/OPN99000001.jspx`에 `bld=OPN/05/05000000/opn05000000t1_01`을
  주면 `{"output": [{"noti_no": ..., "title": ..., "noti_dd": "2026/08/20", ...}]}`가 온다.
  보도자료 화면(OPN05000000T1.jsp)이 이 서블릿으로 표를 그린다. 사이드카 발동, 결산실적,
  신규상장 같은 시장 조치가 실린다.
- 금감원: `GET /fss/bbs/B0000188/list.do?menuNo=200218`이 게시판 표 HTML이다. 행마다
  `view.do?nttId=...` 링크와 YYYY-MM-DD 날짜가 있다. 홈페이지 개편 후 RSS가 없다.

두 곳 다 발행 **날짜**만 주고 시각은 주지 않는다. 날짜의 기준 시간대는 제공처가 정한다는
수집기 규칙대로 KST 자정으로 두고 UTC로 정규화한다. 시각을 지어내는 것이 아니라 제공처가
고시한 날짜를 그 날짜가 속한 시간대로 읽는 것이다.

## 레지스트리는 여기, 수집 규칙은 각자

`LISTING_SOURCES`가 slug로 수집 방법을 고른다. KRX·금감원은 아직 이 파일에 함수로 있고,
네이버 증권 리서치는 클래스로 `collectors/document/naver_research.py`에 있다. 목록만 읽는
KRX·금감원과 달리 네이버는 **상세를 한 번 더 받아** 요약을 채우고 그것이 `ListingSource.enrich`
단계다. 나머지를 클래스로 옮기는 계획은 `docs/collectors-class-migration.md`에 있다.
"""

import json
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from curl_cffi.curl import CurlError
from pydantic import BaseModel, ConfigDict, field_validator
from scrapling import Selector
from scrapling.fetchers import Fetcher

from modules.collectors.document.naver_research import (
    NAVER_RESEARCH_CATEGORIES,
    NAVER_RESEARCH_SLUG_PREFIX,
    NaverResearchCollector,
)
from modules.collectors.documents import (
    IMPERSONATE,
    MAX_ITEMS_PER_FEED,
    REQUEST_TIMEOUT_SECONDS,
    Connection,
    DocumentHTTPError,
    DocumentPayloadError,
    FeedItem,
    FeedResponse,
    FeedSource,
    kst_midnight_utc,
    normalize_text,
)

logger = logging.getLogger(__name__)

KST = ZoneInfo("Asia/Seoul")

# KRX 조회 구간 패딩. 목록 조회가 날짜 구간을 요구하는데, 연휴가 껴도 직전 발표가
# 구간 안에 들어와야 0건이 정상인지 판단할 수 있다. `boe.FETCH_PADDING_DAYS`와 같은 취지다.
KRX_FETCH_PADDING_DAYS = 14

# KRX 보도자료 화면이 쓰는 데이터 좌표. 화면(OPN05000000T1.jsp)의 `$.board.init`이 선언한다.
KRX_PRESS_BLD = "OPN/05/05000000/opn05000000t1_01"

# 사람이 보도자료를 찾아 들어갈 화면. KRX는 문서별 GET 딥링크가 없어(내용 조회도 POST)
# canonical_url은 이 화면에 noti_no를 붙인 합성 URL이다. 문서를 특정하지만 클릭 한 번에
# 열리지는 않는다는 한계를 안다.
KRX_PRESS_PAGE = "https://open.krx.co.kr/contents/OPN/05/05000000/OPN05000000T1.jsp"


class ListingSource(BaseModel):
    """피드 없는 출처 하나의 수집 방법. `LISTING_SOURCES` 레지스트리의 항목이다.

    `enrich`는 목록이 주지 않는 것(요약 문단 등)을 **새 항목에만** 상세 요청으로 채우는
    선택 단계다. 연결을 받는 이유는 이미 있는 `(source_slug, external_id)`를 먼저 빼기
    위해서다 — 기존 항목을 목록 정보로 다시 upsert하면 `content_hash`가 달라져 상세 요약이
    지워지고 재평가가 돈다. DAG은 이 단계를 트랜잭션 바깥에서 부른다(HTTP를 트랜잭션 안에
    두지 않는다).
    """

    model_config = ConfigDict(frozen=True)

    slug: str
    fetch: Callable[[FeedSource], FeedResponse]
    parse: Callable[[bytes], tuple[tuple[FeedItem, ...], bool]]
    enrich: Callable[[Connection, FeedSource, tuple[FeedItem, ...]], tuple[FeedItem, ...]] | None = None


class KrxNotice(BaseModel):
    """KRX JSON 서블릿 응답의 행 하나. 우리가 쓰는 칸만 받는다."""

    model_config = ConfigDict(frozen=True)

    noti_no: str
    title: str
    contn: str | None = None
    noti_dd: str

    @field_validator("noti_dd")
    @classmethod
    def require_slash_date(cls, raw: str) -> str:
        # `2026/08/20` 모양만 받는다. 모르는 표기를 조용히 엉뚱한 날짜로 만들지 않는다.
        datetime.strptime(raw, "%Y/%m/%d")  # noqa: DTZ007 — 검증만 하고 값은 그대로 둔다
        return raw


def fetch_krx(source: FeedSource) -> FeedResponse:
    """KRX 보도자료 목록 JSON을 한 번 받아 온다. 조회 구간은 KST 오늘까지 14일이다."""
    started_at = datetime.now(UTC)
    today = started_at.astimezone(KST).date()
    fromdate = today - timedelta(days=KRX_FETCH_PADDING_DAYS)
    try:
        response = Fetcher.post(
            source.feed_url,
            data={
                "bld": KRX_PRESS_BLD,
                "sch_tp": "title",
                "sch_word": "",
                "fromdate": fromdate.strftime("%Y%m%d"),
                "todate": today.strftime("%Y%m%d"),
                "curPage": "1",
                "pageSize": str(MAX_ITEMS_PER_FEED),
            },
            impersonate=IMPERSONATE,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except CurlError as error:
        raise ConnectionError(f"Listing request failed for {source.slug}: {error}") from error

    if not 200 <= response.status < 300:
        raise DocumentHTTPError(response.status)

    return FeedResponse(
        slug=source.slug,
        url=source.feed_url,
        body=response.body,
        status=response.status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


def parse_krx(body: bytes) -> tuple[tuple[FeedItem, ...], bool]:
    """KRX JSON 목록에서 항목을 뽑는다. (항목, 잘렸는지)를 돌려준다.

    응답이 JSON이 아니거나 `output` 키가 없으면 실패시킨다. 주소나 서블릿 계약이 바뀌면
    HTML 안내가 200으로 올 수 있고, 조용히 0건으로 넘기면 몇 달째 비어 있어도 알 수 없다.

    `output`이 빈 목록인 것은 실패가 아니다. 14일 패딩 안에 발표가 없는 연휴가 정상이다.
    """
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DocumentPayloadError(f"KRX listing body is not valid JSON: {error}") from None
    if not isinstance(payload, dict) or "output" not in payload:
        raise DocumentPayloadError("KRX listing JSON has no 'output' key")

    rows = payload["output"]
    truncated = len(rows) > MAX_ITEMS_PER_FEED

    items: list[FeedItem] = []
    for row in rows[:MAX_ITEMS_PER_FEED]:
        notice = KrxNotice.model_validate(row)
        title = normalize_text(notice.title)
        if not title:
            continue
        published_day = datetime.strptime(notice.noti_dd, "%Y/%m/%d").date()  # noqa: DTZ007
        items.append(
            FeedItem(
                external_id=notice.noti_no,
                canonical_url=f"{KRX_PRESS_PAGE}?noti_no={notice.noti_no}",
                title=title,
                summary=normalize_text(notice.contn),
                # 고시일은 KRX가 KST 기준으로 정한 날짜다.
                published_at=kst_midnight_utc(published_day),
            )
        )
    return tuple(items), truncated


def fetch_fss(source: FeedSource) -> FeedResponse:
    """금감원 보도자료 목록 HTML을 한 번 받아 온다. `fetch_feed`와 같은 GET이다."""
    started_at = datetime.now(UTC)
    try:
        response = Fetcher.get(source.feed_url, impersonate=IMPERSONATE, timeout=REQUEST_TIMEOUT_SECONDS)
    except CurlError as error:
        raise ConnectionError(f"Listing request failed for {source.slug}: {error}") from error

    if not 200 <= response.status < 300:
        raise DocumentHTTPError(response.status)

    return FeedResponse(
        slug=source.slug,
        url=source.feed_url,
        body=response.body,
        status=response.status,
        started_at=started_at,
        completed_at=datetime.now(UTC),
    )


def parse_fss(body: bytes) -> tuple[tuple[FeedItem, ...], bool]:
    """금감원 eGov 게시판 표에서 항목을 뽑는다. (항목, 잘렸는지)를 돌려준다.

    **행이 하나도 안 나오면 실패시킨다.** 이 게시판은 이만 건 넘게 쌓여 있어 목록이 비는
    일이 없다. 0행은 새 문서가 없는 것이 아니라 마크업이 바뀌어 선택자가 빗나간 것이다.
    """
    document = Selector(content=body.decode("utf-8", errors="replace"))

    items: list[FeedItem] = []
    for row in document.css("tr"):
        anchors = row.css('td.title a[href*="nttId="]')
        if not anchors:
            continue
        anchor = anchors[0]
        href = anchor.attrib.get("href")
        title = normalize_text(anchor.text)
        if not href or not title:
            continue

        url = urljoin("https://www.fss.or.kr/", href)
        # nttId가 게시글 고유키다. menuNo·pageIndex는 화면 상태라 external_id에 넣지 않는다.
        ntt_id = next(
            (part.split("=", 1)[1] for part in href.split("?", 1)[-1].split("&") if part.startswith("nttId=")),
            None,
        )
        if not ntt_id:
            continue

        # 같은 행의 YYYY-MM-DD 칸이 게시일이다. 금감원이 KST 기준으로 정한 날짜다.
        published_at = None
        for cell in row.css("td"):
            text = (cell.text or "").strip()
            try:
                published_at = kst_midnight_utc(date.fromisoformat(text))
                break
            except ValueError:
                continue

        items.append(
            FeedItem(
                external_id=ntt_id,
                canonical_url=url,
                title=title,
                summary=None,
                published_at=published_at,
            )
        )

    if not items:
        raise DocumentPayloadError("FSS listing has no board rows; the markup may have changed")
    return tuple(items), False


def _naver_research_sources() -> dict[str, ListingSource]:
    """네이버 리서치 여섯 카테고리. 수집 규칙은 `collectors/document/naver_research.py`가 갖는다.

    레지스트리는 콜러블을 들고 있고 수집기는 클래스라, 출처마다 객체를 만들어 주는 클래스
    메서드를 끼운다. 다른 출처도 클래스로 옮기면 이 레지스트리 자체를 수집기 팩토리로 바꾼다
    (`docs/collectors-class-migration.md`).
    """
    return {
        f"{NAVER_RESEARCH_SLUG_PREFIX}{category}": ListingSource(
            slug=f"{NAVER_RESEARCH_SLUG_PREFIX}{category}",
            fetch=NaverResearchCollector.fetch_listing,
            parse=NaverResearchCollector.parse,
            enrich=NaverResearchCollector.enrich_listing,
        )
        for category in NAVER_RESEARCH_CATEGORIES
    }


# 피드가 없어 목록으로 붙는 출처. DAG이 slug로 여기를 먼저 보고, 없으면 RSS 경로로 간다.
LISTING_SOURCES: dict[str, ListingSource] = {
    "krx": ListingSource(slug="krx", fetch=fetch_krx, parse=parse_krx),
    "fss": ListingSource(slug="fss", fetch=fetch_fss, parse=parse_fss),
    **_naver_research_sources(),
}
