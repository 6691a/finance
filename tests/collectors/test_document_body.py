import hashlib
import re
from datetime import UTC, datetime
from typing import Self

import pytest
from sqlalchemy import Table

from apps.models.content import DocumentAttachment as AttachmentModel
from modules.collectors.document.body import (
    ATTACHMENT_UPSERT,
    BODY_UPDATE,
    MIN_BODY_LENGTH,
    MIN_PARAGRAPH_LENGTH,
    PENDING_BODIES,
    Attachment,
    BodyCandidate,
    DocumentBody,
    DocumentBodyCollector,
    detail_url,
    extract_body,
    find_attachment_urls,
    find_video_urls,
    is_html_document,
    naver_attachment_urls,
    paragraph_selector,
    pending_bodies,
)
from modules.collectors.document.documents import DocumentGoneError, DocumentPayloadError

FETCHED_AT = datetime(2026, 8, 30, 9, 15, tzinfo=UTC)

LONG = "이것은 문단 하나가 본문으로 인정받을 만큼 충분히 긴 문장이며 숫자와 맥락을 담고 있다."


def page(inner: str, container: str = "article") -> str:
    return f"<html><body><{container}>{inner}</{container}></body></html>"


def paragraphs(count: int) -> str:
    return "".join(f"<p>{LONG} {index}</p>" for index in range(count))


def without_comments(statement: str) -> str:
    """설명 주석을 뗀 SQL. 주석이 규칙을 글로 적고 있어 그대로 찾으면 늘 걸린다."""
    return re.sub(r"(?m)^\s*--.*$", "", statement)


class FakeCursor:
    def __init__(self, rows: list | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.rows = rows or []
        self.rowcount = 1

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: object = ()) -> None:
        self.calls.append((statement, parameters))

    def fetchall(self) -> list:
        return self.rows


class FakeConnection:
    def __init__(self, rows: list | None = None) -> None:
        self.recorded_cursor = FakeCursor(rows)

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


class FakeResponse:
    def __init__(self, body: bytes, headers: dict[str, str] | None = None) -> None:
        self.body = body
        self.headers = headers or {}
        self.status = 200


def test_the_paragraph_ladder_stops_at_the_narrowest_container_that_has_a_body():
    """`body`는 언제나 `main`을 포함한다. 가장 긴 것을 고르면 화면 문구가 늘 함께 들어온다.

    미국 연준 보도자료가 그렇게 "An official website of the United States Government"를
    본문 앞에 달고 저장될 뻔했다(2026-08-30 실측).
    """
    chrome = f"<p>{'화면 안내 문구입니다. ' * 12}</p>"
    html = f"<html><body>{chrome}<main>{paragraphs(4)}</main></body></html>"

    body = extract_body(html)

    assert body is not None
    assert "화면 안내 문구" not in body
    assert body.count("이것은 문단") == 4


def test_a_page_below_the_floor_has_no_body_at_all():
    """금융감독원 게시판은 본문이 첨부에만 있는데 안내 문구 한 문단이 잡힌다.

    하한이 없으면 그 한 문장이 본문으로 저장되고, 검색은 그것을 기사로 읽는다.
    """
    assert MIN_PARAGRAPH_LENGTH < MIN_BODY_LENGTH
    short = "자주쓰는 메뉴를 설정하시거나, 유형별 서비스를 찾아서 바로 이용해보세요."
    assert len(short) >= MIN_PARAGRAPH_LENGTH

    assert extract_body(page(f"<p>{short}</p>")) is None


def test_short_paragraphs_are_not_body():
    assert extract_body(page("<p>짧다</p>" * 30)) is None


def test_scripts_and_menus_never_reach_the_body():
    noisy = (
        "<script>var tracking = 1;</script>"
        "<style>.a{color:red}</style>"
        "<nav><p>메뉴 항목이 길게 늘어서 있는 내비게이션 문단입니다 정말로</p></nav>"
        f"{paragraphs(4)}"
    )

    body = extract_body(page(noisy))

    assert body is not None
    assert "tracking" not in body
    assert "color:red" not in body
    assert "내비게이션" not in body


def test_the_body_has_no_length_cap():
    """저장은 원본 보존이다. 프롬프트에 얼마를 실을지는 읽는 쪽이 정한다."""
    body = extract_body(page(paragraphs(400)))

    assert body is not None
    assert len(body) > 20_000


def test_only_the_listed_sources_read_list_items_as_body():
    """`li`를 기본으로 열면 메뉴와 관련기사가 함께 들어온다."""
    assert paragraph_selector("cnbc") == "p"
    assert paragraph_selector("whitehouse") == "p, li"


def test_attachments_are_found_without_a_per_source_rule():
    """한국은행·금감원·BOJ 셋 다 확장자나 내려받기 경로를 링크에 그대로 노출한다."""
    html = page(
        '<a href="/fileSrc/portal/abc/1/2024.hwp">붙임1</a>'
        '<a href="/fileSrc/portal/abc/1/2024.hwp">다운로드</a>'
        '<a href="/fss/cmmn/file/fileDown.do?atchFileId=x&fileSn=1">보도자료</a>'
        '<a href="data/rev26e11.pdf">Full Text</a>'
        '<a href="/about">회사 소개</a>'
    )

    found = find_attachment_urls(html, "https://example.org/board/view.do")

    assert found == (
        "https://example.org/fileSrc/portal/abc/1/2024.hwp",
        "https://example.org/fss/cmmn/file/fileDown.do?atchFileId=x&fileSn=1",
        "https://example.org/board/data/rev26e11.pdf",
    )


def test_attachments_survive_the_elements_the_body_rule_throws_away():
    """한국은행 게시판은 첨부를 `<form>` 안에 둔다. 그 `<form>`이 페이지의 98%다.

    본문에서 걷어내는 요소를 첨부 찾기에도 적용하면 그 130건이 통째로 `empty`가 되고
    PDF도 영영 안 받는다(2026-08-30 실측: 388,802자 → 7,136자, 첨부 링크 8개 → 0개).
    """
    html = (
        "<html><body><form><a href='/fileSrc/a/2024.hwp'>붙임</a></form>"
        "<figure><video src='/m/clip.mp4'></video></figure></body></html>"
    )

    assert find_attachment_urls(html, "https://www.bok.or.kr/v.do") == ("https://www.bok.or.kr/fileSrc/a/2024.hwp",)
    assert find_video_urls(html, "https://www.bok.or.kr/v.do") == ("https://www.bok.or.kr/m/clip.mp4",)


def test_an_outbound_pdf_in_the_article_body_is_not_an_attachment():
    """기사가 인용한 남의 PDF는 그 문서의 첨부가 아니다.

    **운영 실측(2026-08-30)**: 저장된 파일 23건 중 5건이 남의 것이었다. BBC 기사가
    courtlistener·cdt.org·parliament.uk·nfcc.org.uk의 PDF를 걸었고, BEA 보도자료가
    census.gov 파일을 걸었다. NPR 기사가 인용한 개인 사이트 PDF는 403으로 죽어서
    첨부 실패로 매번 집계됐다.

    **하위 도메인은 같은 것으로 본다.** BEA는 자기 파일 일부를 apps.bea.gov에 둔다.
    """
    html = page(
        '<a href="https://apps.bea.gov/f/a.pdf">우리 첨부</a>'
        '<a href="/sites/default/files/b.xlsx">우리 첨부</a>'
        '<a href="https://www.census.gov/x/c.pdf">남의 자료</a>'
        '<a href="https://nfcc.org.uk/d.pdf">남의 자료</a>'
    )

    assert find_attachment_urls(html, "https://www.bea.gov/news/2026/gdp") == (
        "https://apps.bea.gov/f/a.pdf",
        "https://www.bea.gov/sites/default/files/b.xlsx",
    )


def test_a_two_label_public_suffix_is_not_treated_as_the_document_domain():
    """`www.bbc.co.uk`에서 뒤 두 마디만 떼면 `co.uk`가 되어 영국 전체가 같은 도메인이 된다."""
    html = page('<a href="https://nfcc.org.uk/d.pdf">남의 자료</a><a href="/news/e.pdf">우리 첨부</a>')

    assert find_attachment_urls(html, "https://www.bbc.co.uk/news/articles/x") == (
        "https://www.bbc.co.uk/news/e.pdf",
    )


def test_a_canonical_url_without_a_scheme_still_resolves_its_attachments():
    """최초 수집 실행에 스킴 없는 URL이 한 건 들어왔다(문서 66, BEA, 2026-08-17 실측).

    `urljoin`은 스킴 없는 base로는 상대 링크를 못 푼다. 그대로 두면 `/sites/...`가 curl에
    넘어가 `No host part in the URL`로 죽는다 — 그 문서의 첨부 다섯이 매 실행 실패했다.
    """
    html = page('<a href="/sites/default/files/2026-02/gdp4q25-adv.pdf">붙임</a>')

    assert find_attachment_urls(html, "www.bea.gov/news/2026/gdp-advance-estimate") == (
        "https://www.bea.gov/sites/default/files/2026-02/gdp4q25-adv.pdf",
    )


def test_a_pdf_viewer_link_is_not_an_attachment():
    """한국은행은 같은 PDF를 뷰어 링크로도 건다. 확장자가 질의 문자열에 있을 뿐 파일이 아니다."""
    html = page('<a href="/static/pdfjs/viewer.html?file=%2FfileSrc%2Fa.pdf">뷰어</a>')

    assert find_attachment_urls(html, "https://www.bok.or.kr/x") == ()


def test_naver_research_reads_the_attachment_from_its_own_json():
    payload = b'{"researchContent": {"content": "\\uc694\\uc57d", "attachUrl": "https://s.pstatic.net/a.pdf"}}'

    assert naver_attachment_urls(payload) == ("https://s.pstatic.net/a.pdf",)


def test_naver_research_without_an_attachment_is_not_a_failure():
    assert naver_attachment_urls(b'{"researchContent": {"content": null}}') == ()


def test_naver_research_empty_object_means_the_report_is_gone():
    """지워진 `researchId`에 네이버는 404가 아니라 200에 `{}`로 답한다(문서 74244, 2026-08-31 실측).

    형식이 바뀐 것(`DocumentPayloadError`)과 갈라 올려야 DAG이 행을 지울지 다시 집을지 정한다.
    """
    with pytest.raises(DocumentGoneError):
        naver_attachment_urls(b"{}")


def test_naver_research_missing_content_key_is_still_a_failure():
    """빈 객체가 아닌데 키가 없으면 제공처 형식이 바뀐 것이다."""
    with pytest.raises(DocumentPayloadError):
        naver_attachment_urls(b'{"researchId": 40006}')


def test_naver_research_html_instead_of_json_is_a_failure():
    """경로가 바뀌면 HTML 안내가 200으로 온다. 조용히 0건으로 넘기면 몇 달째 비어도 모른다."""
    with pytest.raises(DocumentPayloadError):
        naver_attachment_urls(b"<html>Not found</html>")


def test_the_url_we_fetch_always_carries_a_scheme():
    """스킴이 없으면 curl이 `https`가 아니라 `http`로 붙는다.

    운영 실측(2026-08-30): 문서 66(`www.bea.gov/news/...`)의 본문 요청이 컨테이너에서
    30초 타임아웃 × 3회로 죽었다. 한 실행에 90초를 먹고 `body_status`가 NULL로 남아
    매 실행 같은 자리에서 되풀이된다.

    `urljoin` base만 고쳐서는 부족하다. **받으러 가는 주소 자체**가 절대 주소여야 한다.
    """
    assert detail_url("bea", "www.bea.gov/news/2026/gdp") == "https://www.bea.gov/news/2026/gdp"
    assert detail_url("naver_research_economy", "m.stock.naver.com/research/economy/1") == (
        "https://m.stock.naver.com/api/research/economy/1"
    )


def test_the_naver_detail_url_is_the_json_behind_the_screen():
    assert (
        detail_url("naver_research_economy", "https://m.stock.naver.com/research/economy/13742")
        == "https://m.stock.naver.com/api/research/economy/13742"
    )
    assert detail_url("cnbc", "https://www.cnbc.com/a.html") == "https://www.cnbc.com/a.html"


def test_a_video_tag_wins_over_everything_else():
    html = page('<video src="/media/clip.mp4"></video><iframe src="https://www.youtube.com/embed/x"></iframe>')

    assert find_video_urls(html, "https://example.org/a") == ("https://example.org/media/clip.mp4",)


def test_only_known_players_count_as_a_video_iframe():
    """연합뉴스 기사 페이지의 첫 `iframe`이 게임 위젯이었다(2026-08-30 실측).

    아무 `iframe`이나 집으면 그 위젯이 영상으로 저장된다.
    """
    html = page(
        '<iframe src="https://games.yna.co.kr/embed?screen=pc"></iframe>'
        '<iframe src="https://www.youtube.com/embed/abc"></iframe>'
    )

    assert find_video_urls(html, "https://www.yna.co.kr/view/AKR1") == ("https://www.youtube.com/embed/abc",)


def test_a_widget_iframe_alone_is_not_a_video():
    html = page('<iframe src="https://games.yna.co.kr/embed?screen=pc"></iframe>')

    assert find_video_urls(html, "https://www.yna.co.kr/view/AKR1") == ()


def test_a_media_url_in_the_markup_is_taken_when_there_is_no_player():
    """CNBC가 재생 주소를 HTML에 그대로 싣는다(2026-08-30 실측)."""
    html = page('<div data-src="https://pdl.cnbc.com/7000/hd_L.mp4"></div>')

    assert find_video_urls(html, "https://www.cnbc.com/a.html") == ("https://pdl.cnbc.com/7000/hd_L.mp4",)


def test_a_video_page_that_hydrates_its_player_falls_back_to_its_own_url():
    """BBC 영상 페이지에는 `og:video`도 `iframe`도 `<video>`도 `.mp4`도 없다.

    재생기를 JavaScript가 만든다(2026-08-30 실측). 주소 말고 남길 것이 없지만, 그 문서가
    영상이라는 사실 자체가 읽는 쪽에 필요하다.
    """
    url = "https://www.bbc.co.uk/news/videos/c4gjn4ezljno"

    assert find_video_urls(page(paragraphs(3)), url) == (url,)


def test_an_article_page_is_not_guessed_to_be_a_video():
    url = "https://www.bbc.co.uk/news/articles/cy9zjgv9lgdo"

    assert find_video_urls(page(paragraphs(3)), url) == ()


def test_a_video_document_still_keeps_its_body():
    """영상 페이지에도 설명 문단이 있다. 영상이라고 본문을 버리지 않는다."""
    url = "https://www.bbc.co.uk/news/videos/c4gjn4ezljno"
    html = page(paragraphs(4))

    body = extract_body(html)
    videos = find_video_urls(html, url)

    assert body is not None
    assert videos == (url,)


def test_a_source_without_a_document_url_is_settled_without_a_request(monkeypatch):
    """KRX 보도자료는 내용 조회도 POST라 문서별 GET 딥링크가 없다.

    요청해 봐야 목록 화면이 온다. 받아 보지 않고 확정해 36건의 헛요청을 없앤다.
    """

    def explode(*args: object, **kwargs: object):
        raise AssertionError("KRX must not be requested")

    monkeypatch.setattr("modules.collectors.document.body.fetch_url", explode)

    result = DocumentBodyCollector().collect(
        BodyCandidate(id=1, source_slug="krx", canonical_url="https://open.krx.co.kr/x.jsp?noti_no=1")
    )

    assert result == DocumentBody(document_id=1, status="unavailable")


def test_a_page_whose_body_lives_in_an_attachment_is_marked_attachment_only(monkeypatch, tmp_path):
    html = page('<a href="/f/report.pdf">붙임</a>')
    responses = iter([FakeResponse(html.encode()), FakeResponse(b"%PDF-1.7 x", {"Content-Type": "application/pdf"})])
    monkeypatch.setattr(
        "modules.collectors.document.body.fetch_url", lambda *args, **kwargs: next(responses)
    )

    result = DocumentBodyCollector(tmp_path).collect(
        BodyCandidate(id=7, source_slug="boj", canonical_url="https://www.boj.or.jp/a.htm")
    )

    assert result.status == "attachment_only"
    assert result.body is None
    assert result.file_urls == ("https://www.boj.or.jp/f/report.pdf",)

    # 파일은 별도 요청이다. 그 실패가 본문 저장을 되돌리지 않게 나뉘어 있다.
    candidate = BodyCandidate(id=7, source_slug="boj", canonical_url="https://www.boj.or.jp/a.htm")
    attachment = DocumentBodyCollector(tmp_path).download(candidate, result.file_urls[0], 0, FETCHED_AT)
    stored = tmp_path / attachment.storage_path
    assert stored.read_bytes() == b"%PDF-1.7 x"
    assert stored.suffix == ".pdf"


def test_a_document_url_that_is_itself_a_file_is_never_read_as_body(monkeypatch, tmp_path):
    """BOJ의 `canonical_url`이 PDF다. 본문으로 읽으면 PDF 바이트가 그대로 저장된다.

    2026-08-31 운영 실측에서 그렇게 저장된 문서가 22건이었다 — `body`가 `%PDF-1.7 %����`로
    시작하고 평균 748,010자, 최대 678만 자다. BM25 색인이 그 바이너리를 형태소 분석하느라
    분당 15행으로 기어갔다.
    """
    monkeypatch.setattr(
        "modules.collectors.document.body.fetch_url",
        lambda *args, **kwargs: FakeResponse(b"%PDF-1.7 " + b"x" * 5000, {"content-type": "application/pdf"}),
    )

    result = DocumentBodyCollector(tmp_path).collect(
        BodyCandidate(id=91547, source_slug="boj", canonical_url="https://www.boj.or.jp/en/mopo/outlook/g.pdf")
    )

    assert result.body is None
    # 원문은 그 파일이다. 첨부로 받아 두면 첨부 파서가 텍스트로 바꾼다.
    assert result.status == "attachment_only"
    assert result.file_urls == ("https://www.boj.or.jp/en/mopo/outlook/g.pdf",)


def test_the_payload_decides_before_the_header_does():
    """제공처가 PDF에 `text/html`을 붙여 주는 경우가 있다."""
    assert not is_html_document("text/html; charset=utf-8", b"%PDF-1.7 ...")
    assert not is_html_document(None, b"PK\x03\x04hwpx")
    # 헤더가 없으면 예전처럼 HTML로 본다. 스킴 없는 주소 하나 때문에 전체를 막지 않는다.
    assert is_html_document(None, "<html><body><p>본문</p></body></html>".encode())
    assert is_html_document("text/html", b"<html>")
    assert not is_html_document("application/pdf", b"<html>")


def test_a_page_with_neither_body_nor_attachment_is_empty_not_a_failure(monkeypatch, tmp_path):
    """목록 화면을 가리키는 문서가 있다. 새 글이 없는 것과 같아 실패가 아니다."""
    monkeypatch.setattr(
        "modules.collectors.document.body.fetch_url",
        lambda *args, **kwargs: FakeResponse(page("<p>짧다</p>").encode()),
    )

    result = DocumentBodyCollector(tmp_path).collect(
        BodyCandidate(id=8, source_slug="census", canonical_url="https://www.census.gov/i.html")
    )

    assert result.status == "empty"
    assert result.file_urls == ()
    assert result.video_urls == ()


def test_the_download_path_is_the_document_folder(monkeypatch, tmp_path):
    """디스크만 보고 어느 출처의 어느 문서인지 알 수 있어야 한다.

    내용 해시로 두면 `00/00ab3f….pdf`가 되어 매번 DB를 조회해야 무엇인지 안다. 백업을
    뒤지거나 용량이 튀었을 때 원인을 못 짚는다.
    """
    monkeypatch.setattr(
        "modules.collectors.document.body.fetch_url",
        lambda *args, **kwargs: FakeResponse(b"%PDF-1.7", {"Content-Type": "application/pdf"}),
    )
    candidate = BodyCandidate(id=1042, source_slug="boj", canonical_url="https://www.boj.or.jp/a.htm")

    attachment = DocumentBodyCollector(tmp_path).download(candidate, "https://x.example/a.pdf", 0, FETCHED_AT)

    assert attachment.storage_path == "documents/boj/1042/0.pdf"
    assert (tmp_path / attachment.storage_path).read_bytes() == b"%PDF-1.7"
    assert attachment.byte_size == len(b"%PDF-1.7")
    assert attachment.fetched_at == FETCHED_AT
    # 해시는 경로에서 빠졌지만 컬럼에는 남는다. 중복은 나중에 조회로 찾는다.
    assert attachment.sha256 == hashlib.sha256(b"%PDF-1.7").hexdigest()


def test_every_attachment_of_one_document_lands_in_the_same_folder(monkeypatch, tmp_path):
    """문서 하나가 폴더 하나다. 첨부가 여섯이어도 한자리에 모인다(금감원이 그렇다)."""
    monkeypatch.setattr(
        "modules.collectors.document.body.fetch_url",
        lambda *args, **kwargs: FakeResponse(b"x", {"Content-Type": "application/pdf"}),
    )
    candidate = BodyCandidate(id=225621, source_slug="fss", canonical_url="https://www.fss.or.kr/v.do")
    collector = DocumentBodyCollector(tmp_path)

    paths = [collector.download(candidate, f"https://x.example/{index}.pdf", index).storage_path for index in range(3)]

    assert paths == ["documents/fss/225621/0.pdf", "documents/fss/225621/1.pdf", "documents/fss/225621/2.pdf"]
    assert sorted(path.name for path in (tmp_path / "documents/fss/225621").iterdir()) == [
        "0.pdf",
        "1.pdf",
        "2.pdf",
    ]


def test_two_documents_pointing_at_one_file_each_keep_their_own_copy(monkeypatch, tmp_path):
    """중복 제거를 버린 대가다. 그 대신 경로가 문서를 가리킨다.

    겹치는 파일이 얼마나 되는지는 재 본 적이 없다. 안 잰 이득을 위해 읽기 어려운 경로를
    고르지 않는다. 필요해지면 `sha256` 컬럼으로 조회해 찾는다.
    """
    monkeypatch.setattr(
        "modules.collectors.document.body.fetch_url",
        lambda *args, **kwargs: FakeResponse(b"same bytes", {"Content-Type": "application/pdf"}),
    )
    collector = DocumentBodyCollector(tmp_path)
    first = collector.download(
        BodyCandidate(id=1, source_slug="boj", canonical_url="https://a"), "https://x.example/a.pdf", 0
    )
    second = collector.download(
        BodyCandidate(id=2, source_slug="boj", canonical_url="https://b"), "https://x.example/a.pdf", 0
    )

    assert first.storage_path != second.storage_path
    assert first.sha256 == second.sha256
    assert len(list(tmp_path.rglob("*.pdf"))) == 2


def test_the_filename_prefers_what_the_provider_declared(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "modules.collectors.document.body.fetch_url",
        lambda *args, **kwargs: FakeResponse(
            b"x", {"Content-Disposition": 'attachment; filename="보도자료.hwp"', "Content-Type": "application/x-hwp"}
        ),
    )

    candidate = BodyCandidate(id=9, source_slug="fss", canonical_url="https://www.fss.or.kr/v.do")
    attachment = DocumentBodyCollector(tmp_path).download(
        candidate, "https://x.example/fileDown.do?id=1", 0, FETCHED_AT
    )

    # 제공처 이름은 컬럼에 남기고 경로에는 안 쓴다. 한글·공백·괄호가 섞여 있고 길이도 길다.
    assert attachment.filename == "보도자료.hwp"
    assert attachment.storage_path == "documents/fss/9/0.hwp"


def test_the_queue_is_the_backfill():
    """`body IS NULL`로 고르면 받을 수 없는 문서를 매시간 다시 친다."""
    statement = without_comments(PENDING_BODIES)
    assert "body_status IS NULL" in statement
    assert "body IS NULL" not in statement
    assert "canonical_document_id IS NULL" in statement

    connection = FakeConnection([(3, "bbc_business", "https://b.example/a")])
    assert pending_bodies(connection, 50) == (
        BodyCandidate(id=3, source_slug="bbc_business", canonical_url="https://b.example/a"),
    )
    assert connection.recorded_cursor.calls[0] == (PENDING_BODIES, (50,))


def test_the_update_never_touches_the_content_hash():
    """해시는 제목과 요약만 본다. 여기서 건드리면 문서 전체가 재평가 대상이 된다."""
    statement = without_comments(BODY_UPDATE)
    assert "content_hash" not in statement
    assert "body_status IS NULL" in statement


def test_the_update_touches_only_the_body_and_its_status():
    """`content_level`은 지웠다. 읽는 코드가 없는데 CHECK만 걸려 태스크를 죽였다."""
    statement = without_comments(BODY_UPDATE)

    assert "content_level" not in statement
    assert "SET body = %(body)s" in statement
    assert "body_status = %(body_status)s" in statement


def test_the_queue_respects_a_source_that_forbids_full_text():
    """`collection_mode`를 내리는 것이 본문 수집을 막는 유일한 손잡이다.

    설계가 "한 곳이 본문 자동수집을 막으면 `collection_mode`를 내리는 것으로 끝나야
    한다"고 못 박았는데, 큐가 그 값을 안 보면 내려도 이 DAG은 계속 원문을 받는다.
    """
    statement = without_comments(PENDING_BODIES)
    assert "document_source" in statement
    assert "metadata_only" in statement


def test_storing_the_body_also_lands_the_video_links():
    """영상은 내려받지 않으므로 본문과 같은 트랜잭션에서 끝난다."""
    connection = FakeConnection()
    result = DocumentBody(
        document_id=5,
        status="ok",
        body="본문",
        file_urls=("https://x.example/a.pdf",),
        video_urls=("https://x.example/v",),
    )

    assert DocumentBodyCollector.store_body(connection, result) == 1

    calls = connection.recorded_cursor.calls
    assert calls[0][0] is BODY_UPDATE
    assert calls[0][1] == {"body": "본문", "body_status": "ok", "document_id": 5}
    assert [call[0] for call in calls[1:]] == [ATTACHMENT_UPSERT]
    video = calls[1][1]
    assert video[2] == "video"
    # 파일이 앞자리를 쓴다. 파일 저장이 뒤에 와도 순서는 페이지에 나온 차례다.
    assert video[1] == 1
    # 영상은 내려받지 않으므로 저장 경로가 없다. DB CHECK와 같은 사실이다.
    assert video[4] is None


def test_a_file_is_attached_in_its_own_transaction():
    """첨부 하나가 죽었다고 어렵게 받은 본문을 버리지 않는다."""
    connection = FakeConnection()
    attachment = Attachment(
        position=0,
        kind="file",
        url="https://x.example/a.pdf",
        storage_path="documents/ab/abcd.pdf",
        filename="a.pdf",
        media_type="application/pdf",
        byte_size=3,
        sha256="abcd",
        fetched_at=FETCHED_AT,
    )

    DocumentBodyCollector.store_attachment(connection, 5, attachment)

    statement, parameters = connection.recorded_cursor.calls[0]
    assert statement is ATTACHMENT_UPSERT
    assert parameters == (5, 0, "file", attachment.url, "documents/ab/abcd.pdf", "a.pdf", "application/pdf", 3, "abcd", FETCHED_AT)


def inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    names = re.sub(r"--[^\n]*", "", columns.group(1))
    return tuple(name.strip() for name in names.split(",") if name.strip())


def test_deleting_a_document_unlinks_its_duplicates_before_the_row_goes():
    """대표를 지우면 그것을 가리키던 중복이 대표로 돌아와 본문·평가 큐에 다시 선다.

    `canonical_document_id`는 RESTRICT라 먼저 끊지 않으면 DELETE가 막힌다. 지워진 74244를
    살아 있는 57292(같은 리포트의 첫 게시)가 가리키고 있었다(2026-08-31 실측).
    """
    connection = FakeConnection()

    DocumentBodyCollector.delete_document(connection, 74244)

    calls = [(without_comments(statement).split(), parameters) for statement, parameters in connection.recorded_cursor.calls]
    assert [parameters for _, parameters in calls] == [(74244,), (74244,)]
    unlink, delete = (words for words, _ in calls)
    assert unlink[:2] == ["UPDATE", "document"]
    assert "canonical_document_id = NULL" in " ".join(unlink)
    assert "WHERE canonical_document_id = %s" in " ".join(unlink)
    assert delete[:3] == ["DELETE", "FROM", "document"]
    assert "WHERE id = %s" in " ".join(delete)


def test_the_attachment_upsert_matches_the_model_and_its_natural_key():
    """수집기는 문자열 SQL을 쓴다. 모델과 어긋나면 저장 시점에야 안다."""
    table: Table = AttachmentModel.__table__
    columns = inserted_columns(ATTACHMENT_UPSERT)

    assert set(columns) <= set(table.columns.keys())
    assert ATTACHMENT_UPSERT.count("%s") == len(columns)
    assert "ON CONFLICT (document_id, url)" in ATTACHMENT_UPSERT
