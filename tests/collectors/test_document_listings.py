from datetime import UTC, datetime
from typing import Any, Self

import pytest

from modules.collectors.document.document_listings import (
    LISTING_SOURCES,
    parse_fss,
    parse_krx,
)
from modules.collectors.document.documents import (
    EXISTING_EXTERNAL_IDS,
    TAGGABLE_INSTRUMENTS,
    DocumentPayloadError,
    FeedSource,
)
from modules.collectors.document.naver_research import (
    NAVER_RESEARCH_CATEGORIES,
    NaverResearchCollector,
    NaverResearchDetail,
)

# 실측 응답(2026-08-20)을 줄인 것. 칸 구성은 실제와 같다.
KRX_JSON = """{"output":[
  {"rn":"1","totCnt":"2","hpage_bbs_tp_cd":"0013","noti_no":"2026081993",
   "title":"유가증권시장 매도사이드카(프로그램매매 매도호가 효력정지) 발동",
   "creat_ddtm":"2026/08/19","contn":"첨부 참조","noti_dd":"2026/08/19",
   "use_yn":"Y","opn_yn":"Y","inq_cnt":"82"},
  {"rn":"2","totCnt":"2","hpage_bbs_tp_cd":"0013","noti_no":"2026081900",
   "title":"㈜기도산업 코스닥시장 신규상장",
   "creat_ddtm":"2026/08/19","contn":"첨부 참조","noti_dd":"2026/08/19",
   "use_yn":"Y","opn_yn":"Y","inq_cnt":"19"}
]}"""

FSS_HTML = """<html><body><table><tbody>
<tr>
  <td class="num">20784</td>
  <td class="title"><a href="/fss/bbs/B0000188/view.do?nttId=223828&menuNo=200218&pageIndex=1">「비청산 장외파생상품거래 증거금 교환제도에 대한 가이드라인」 연장</a></td>
  <td>자본시장감독국</td>
  <td>2026-08-20</td>
  <td><a href="/fss/cmmn/file/fileDown.do?atchFileId=x&fileSn=1">첨부</a></td>
</tr>
<tr>
  <td class="num">20783</td>
  <td class="title"><a href="/fss/bbs/B0000188/view.do?nttId=223674&menuNo=200218&pageIndex=1">「2026 금융권 공동채용 박람회」 개최</a></td>
  <td>총무국</td>
  <td>2026-08-19</td>
  <td></td>
</tr>
</tbody></table></body></html>"""


def test_registry_covers_krx_fss_and_naver_research():
    # DAG이 이 slug들을 목록 수집으로 보낸다. 시드의 slug와 같아야 한다.
    assert set(LISTING_SOURCES) == {
        "krx",
        "fss",
        "naver_research_company",
        "naver_research_industry",
        "naver_research_market",
        "naver_research_invest",
        "naver_research_economy",
        "naver_research_debenture",
    }
    # 상세를 받는 것은 네이버뿐이다. KRX·금감원은 목록이 곧 전부다.
    assert LISTING_SOURCES["krx"].enrich is None
    assert LISTING_SOURCES["fss"].enrich is None
    # 클래스 메서드는 접근할 때마다 새로 바인드되므로 `is`가 아니라 `==`로 본다.
    assert all(
        LISTING_SOURCES[f"naver_research_{c}"].enrich == NaverResearchCollector.enrich_listing
        for c in NAVER_RESEARCH_CATEGORIES
    )


def test_parse_krx_reads_notices():
    items, truncated = parse_krx(KRX_JSON.encode("utf-8"))

    assert truncated is False
    assert len(items) == 2
    item = items[0]
    assert item.external_id == "2026081993"
    assert item.canonical_url == (
        "https://open.krx.co.kr/contents/OPN/05/05000000/OPN05000000T1.jsp?noti_no=2026081993"
    )
    assert item.title == "유가증권시장 매도사이드카(프로그램매매 매도호가 효력정지) 발동"
    assert item.summary == "첨부 참조"
    # 고시일은 KST 기준 날짜다. KST 2026-08-19 00:00 = UTC 2026-08-18 15:00.
    assert item.published_at == datetime(2026, 8, 18, 15, 0, tzinfo=UTC)


def test_parse_krx_accepts_an_empty_window():
    # 14일 패딩 안에 발표가 없는 연휴는 정상이다.
    items, truncated = parse_krx(b'{"output": []}')

    assert items == ()
    assert truncated is False


def test_parse_krx_rejects_a_page_that_is_not_json():
    # 주소나 서블릿 계약이 바뀌면 HTML 안내가 200으로 온다.
    with pytest.raises(DocumentPayloadError, match="not valid JSON"):
        parse_krx(b"<!doctype html><html><body>error</body></html>")


def test_parse_krx_rejects_json_without_the_output_key():
    with pytest.raises(DocumentPayloadError, match="no 'output' key"):
        parse_krx(b'{"result": []}')


def test_parse_krx_rejects_an_unknown_date_shape():
    # 모르는 날짜 표기는 조용히 엉뚱한 날짜가 되기 전에 멈춘다.
    body = KRX_JSON.replace("2026/08/19", "20260819").encode("utf-8")
    with pytest.raises(ValueError):
        parse_krx(body)


def test_parse_fss_reads_board_rows():
    items, truncated = parse_fss(FSS_HTML.encode("utf-8"))

    assert truncated is False
    assert len(items) == 2
    item = items[0]
    # nttId가 게시글 고유키다. menuNo·pageIndex는 화면 상태라 들어가지 않는다.
    assert item.external_id == "223828"
    assert item.canonical_url == "https://www.fss.or.kr/fss/bbs/B0000188/view.do?nttId=223828&menuNo=200218&pageIndex=1"
    assert item.title == "「비청산 장외파생상품거래 증거금 교환제도에 대한 가이드라인」 연장"
    assert item.summary is None
    # 게시일은 KST 기준 날짜다. KST 2026-08-20 00:00 = UTC 2026-08-19 15:00.
    assert item.published_at == datetime(2026, 8, 19, 15, 0, tzinfo=UTC)


def test_parse_fss_fails_when_no_rows_match():
    # 이 게시판은 목록이 비는 일이 없다. 0행은 마크업 변경 신호다.
    with pytest.raises(DocumentPayloadError, match="no board rows"):
        parse_fss(b"<html><body><p>redesigned</p></body></html>")


# --- 네이버 증권 리서치 (실측 2026-08-21) -----------------------------------------

NAVER_COMPANY_JSON = """[
  {"researchCategory":"종목분석","category":"종목분석","itemCode":"003550","itemName":"LG",
   "researchId":95810,"title":"엔비디아가 LG를 고른 이유","brokerName":"대신증권",
   "writeDate":"2026-08-21","readCount":"3505","endUrl":"https://m.stock.naver.com/research/company/95810"},
  {"researchCategory":"종목분석","category":"종목분석","itemCode":"388210","itemName":"씨엠티엑스",
   "researchId":95809,"title":"2Q26P Review: 11분기 연속 매출 성장세 지속","brokerName":"유진투자증권",
   "writeDate":"2026-08-21","readCount":"2760","endUrl":"/research/company/95809"}
]"""

NAVER_MARKET_JSON = """[
  {"researchCategory":"시황정보","category":"시황정보","researchId":37231,
   "title":"Global Platform Weekly(8월 3주차)","brokerName":"신한투자증권",
   "writeDate":"2026-08-21","readCount":"510","endUrl":"https://m.stock.naver.com/research/market/37231"}
]"""

NAVER_COMPANY_DETAIL = {
    "researchContent": {
        "itemCode": "003550",
        "itemName": "LG",
        "researchId": 95810,
        "title": "엔비디아가 LG를 고른 이유",
        "brokerName": "대신증권",
        "writeDate": "2026-08-21",
        "readCount": "3505",
        "attachUrl": "https://stock.pstatic.net/stock-research/company/15/20260821_company_35227000.pdf",
        "content": "<p><strong>엔비디아 MOU</strong></p><p><br>- (로봇) 이족보행 로봇을 내년 1분기 중 공개</p>",
        "opinion": "Buy",
        "goalPrice": "130000",
        "prevGoalPrice": "110400",
        "priceAtWriteDate": "110400",
    },
    "researchSummaries": [],
}

NAVER_MARKET_DETAIL = {
    "researchContent": {
        "researchId": 37231,
        "title": "Global Platform Weekly(8월 3주차)",
        "brokerName": "신한투자증권",
        "writeDate": "2026-08-21",
        "content": "<p>전체적 조정 가운데 오픈AI 분기 실적 발표</p>",
    },
    "researchSummaries": [],
}


def naver_source(category: str = "company") -> FeedSource:
    return FeedSource(
        slug=f"naver_research_{category}",
        name=f"네이버 증권 리서치 · {category}",
        source_kind="research",
        country="KR",
        language="ko",
        feed_url=f"https://m.stock.naver.com/api/research/{category}?pageSize=30&page=1",
        collection_mode="feed_content",
    )


# 목록 JSON의 LG(003550)는 마스터에 있고 씨엠티엑스(388210)는 없다.
TAGGABLE = ("003550", "005930")


class FakeCursor:
    """SQL 문자열로 응답을 고른다. `enrich`가 마스터 종목과 기존 id 둘을 조회한다."""

    def __init__(self, existing: list[str], taggable: tuple[str, ...]) -> None:
        self.calls: list[tuple[str, Any]] = []
        self._existing = existing
        self._taggable = taggable
        self._rows: list[tuple] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: Any = ()) -> None:
        self.calls.append((statement, tuple(parameters)))
        if statement is TAGGABLE_INSTRUMENTS:
            self._rows = [(ticker, ticker) for ticker in self._taggable]
        else:
            self._rows = [(external_id,) for external_id in self._existing]

    def fetchall(self) -> list[tuple]:
        return self._rows


class FakeConnection:
    def __init__(self, existing: list[str] | None = None, taggable: tuple[str, ...] = TAGGABLE) -> None:
        self.recorded_cursor = FakeCursor(existing or [], taggable)

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


def fake_detail(details: dict[str, dict]):
    """상세 요청을 가로챈다. 인스턴스 메서드 자리라 첫 인자가 수집기다."""
    requested: list[tuple[str, str, str]] = []

    def fetch(collector: NaverResearchCollector, external_id: str) -> NaverResearchDetail:
        requested.append((collector.slug, collector.category, external_id))
        return NaverResearchDetail.model_validate(details[external_id])

    fetch.requested = requested  # type: ignore[attr-defined]
    return fetch


def test_parse_naver_research_reads_company_reports():
    items, truncated = NaverResearchCollector.parse(NAVER_COMPANY_JSON.encode("utf-8"))

    assert truncated is False
    assert len(items) == 2
    item = items[0]
    assert item.external_id == "95810"
    assert item.canonical_url == "https://m.stock.naver.com/research/company/95810"
    # 종목명은 앞에, 증권사는 끝에 낱말로. 대괄호 말머리는 dedup이 벗긴다.
    assert item.title == "LG: 엔비디아가 LG를 고른 이유 - 대신증권"
    # 요약은 상세가 채운다. 목록만으로는 비어 있다.
    assert item.summary is None
    # 작성일은 KST 기준 날짜다. KST 2026-08-21 00:00 = UTC 2026-08-20 15:00.
    assert item.published_at == datetime(2026, 8, 20, 15, 0, tzinfo=UTC)
    # 종목코드는 저장하지 않고 `enrich`의 마스터 종목 필터에만 쓴다.
    assert item.stock_code == "003550"
    # 상대 경로는 API 뿌리에 붙인다.
    assert items[1].canonical_url == "https://m.stock.naver.com/research/company/95809"


def test_parse_naver_research_reads_reports_without_a_stock():
    (item,), _ = NaverResearchCollector.parse(NAVER_MARKET_JSON.encode("utf-8"))

    assert item.external_id == "37231"
    assert item.title == "Global Platform Weekly(8월 3주차) - 신한투자증권"
    assert item.stock_code is None


def test_parse_naver_research_accepts_an_empty_list():
    # 새벽과 주말에는 리포트가 없다. 빈 배열은 실패가 아니다.
    assert NaverResearchCollector.parse(b"[]") == ((), False)


def test_parse_naver_research_rejects_a_page_that_is_not_a_list():
    with pytest.raises(DocumentPayloadError, match="not an array"):
        NaverResearchCollector.parse(b'{"error": "redesigned"}')
    with pytest.raises(DocumentPayloadError, match="not valid JSON"):
        NaverResearchCollector.parse(b"<html><body>redesigned</body></html>")


def test_parse_naver_research_rejects_an_unknown_date_shape():
    broken = NAVER_MARKET_JSON.replace("2026-08-21", "2026.08.21")

    with pytest.raises(DocumentPayloadError, match="malformed"):
        NaverResearchCollector.parse(broken.encode("utf-8"))


def test_enrich_fetches_details_for_new_items_only(monkeypatch):
    """기존 항목은 결과에서 빠진다. 다시 upsert하면 summary가 NULL로 덮이고 재평가가 돈다."""
    fetch = fake_detail({"95810": NAVER_COMPANY_DETAIL})
    monkeypatch.setattr(NaverResearchCollector, "fetch_detail", fetch)
    items, _ = NaverResearchCollector.parse(NAVER_COMPANY_JSON.encode("utf-8"))
    connection = FakeConnection(existing=["95809"], taggable=("003550", "388210"))

    enriched = NaverResearchCollector(naver_source("company")).enrich(connection, items)

    statement, parameters = connection.recorded_cursor.calls[1]
    assert statement is EXISTING_EXTERNAL_IDS
    assert parameters == ("naver_research_company", ["95810", "95809"])
    assert fetch.requested == [("naver_research_company", "company", "95810")]
    assert [item.external_id for item in enriched] == ["95810"]


def test_enrich_drops_reports_on_stocks_we_do_not_track(monkeypatch):
    """종목분석은 하루 수십 건이고 대부분 마스터에 없는 종목이다. 상세 요청 앞에서 버린다."""
    fetch = fake_detail({"95810": NAVER_COMPANY_DETAIL})
    monkeypatch.setattr(NaverResearchCollector, "fetch_detail", fetch)
    items, _ = NaverResearchCollector.parse(NAVER_COMPANY_JSON.encode("utf-8"))
    connection = FakeConnection()

    enriched = NaverResearchCollector(naver_source("company")).enrich(connection, items)

    assert connection.recorded_cursor.calls[0][0] is TAGGABLE_INSTRUMENTS
    # 씨엠티엑스(388210)는 마스터에 없어 기존 id 조회에도, 상세 요청에도 오르지 않는다.
    assert connection.recorded_cursor.calls[1][1] == ("naver_research_company", ["95810"])
    assert fetch.requested == [("naver_research_company", "company", "95810")]
    assert [item.external_id for item in enriched] == ["95810"]


def test_enrich_keeps_reports_that_are_not_about_one_stock(monkeypatch):
    """시황·투자전략·경제·채권은 시장 전체 이야기다. 카테고리를 끄는 손잡이는 `enabled`다."""
    fetch = fake_detail({"37231": NAVER_MARKET_DETAIL})
    monkeypatch.setattr(NaverResearchCollector, "fetch_detail", fetch)
    items, _ = NaverResearchCollector.parse(NAVER_MARKET_JSON.encode("utf-8"))

    enriched = NaverResearchCollector(naver_source("market")).enrich(FakeConnection(taggable=()), items)

    assert [item.external_id for item in enriched] == ["37231"]


def test_enrich_puts_the_opinion_and_target_before_the_summary(monkeypatch):
    monkeypatch.setattr(NaverResearchCollector, "fetch_detail", fake_detail({"95810": NAVER_COMPANY_DETAIL}))
    items, _ = NaverResearchCollector.parse(NAVER_COMPANY_JSON.encode("utf-8"))

    (item,) = NaverResearchCollector(naver_source("company")).enrich(FakeConnection(existing=["95809"]), items)

    # HTML 태그는 normalize_text가 벗긴다. 파서가 따로 없다.
    assert (
        item.summary
        == "투자의견 Buy · 목표가 130,000 (직전 110,400) · 엔비디아 MOU - (로봇) 이족보행 로봇을 내년 1분기 중 공개"
    )
    # 요약 외의 칸은 그대로다.
    assert item.title == "LG: 엔비디아가 LG를 고른 이유 - 대신증권"


def test_enrich_leaves_market_reports_without_a_target(monkeypatch):
    monkeypatch.setattr(NaverResearchCollector, "fetch_detail", fake_detail({"37231": NAVER_MARKET_DETAIL}))
    items, _ = NaverResearchCollector.parse(NAVER_MARKET_JSON.encode("utf-8"))

    (item,) = NaverResearchCollector(naver_source("market")).enrich(FakeConnection(), items)

    assert item.summary == "전체적 조정 가운데 오픈AI 분기 실적 발표"


def test_enrich_fails_on_a_target_price_that_is_not_a_number(monkeypatch):
    broken = {"researchContent": {**NAVER_COMPANY_DETAIL["researchContent"], "goalPrice": "13만"}}
    monkeypatch.setattr(NaverResearchCollector, "fetch_detail", fake_detail({"95810": broken}))
    items, _ = NaverResearchCollector.parse(NAVER_COMPANY_JSON.encode("utf-8"))

    with pytest.raises(DocumentPayloadError, match="goalPrice"):
        NaverResearchCollector(naver_source("company")).enrich(FakeConnection(existing=["95809"]), items)


def test_the_collector_refuses_a_slug_outside_the_known_categories():
    """카테고리는 slug에서 뽑는다. 모르는 slug면 만들 때 죽는다 — 요청 한 번도 나가지 않는다."""
    with pytest.raises(DocumentPayloadError, match="not a Naver research category"):
        NaverResearchCollector(naver_source("marketinfo"))
