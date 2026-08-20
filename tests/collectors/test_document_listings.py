from datetime import UTC, datetime

import pytest

from modules.collectors.document_listings import (
    LISTING_SOURCES,
    parse_fss,
    parse_krx,
)
from modules.collectors.documents import DocumentPayloadError

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


def test_registry_covers_krx_and_fss():
    # DAG이 이 slug들을 목록 수집으로 보낸다. 시드의 slug와 같아야 한다.
    assert set(LISTING_SOURCES) == {"krx", "fss"}


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
