"""이미 발견한 문서의 본문과 첨부를 받아 채운다.

`documents.py`가 피드와 목록에서 문서를 **발견**하고, 여기가 그 문서 하나하나의 원문을
**받는다.** 설계는 `docs/collection/document-body-collection.md`다.

## 왜 나뉘어 있나

발견은 출처당 요청 한 번이고 본문은 문서당 요청 한 번이다. 한 실행에서 둘을 같이 하면
본문 수백 건의 요청이 다음 시간 피드 수집을 밀고, 재시도가 피드까지 다시 친다. 실패의
단위도 다르다 — 발견은 출처 하나가 죽고 본문은 문서 하나가 죽는다.

`document.body_status`가 그 경계다. 발견은 NULL로 남기고 여기가 채운다. **NULL 집합이 곧
수집 큐이고, 그래서 신규 문서와 과거 문서의 백필이 같은 코드다.**

## 본문을 뽑는 법 (2026-08-30 실측)

출처 15곳에 실제로 요청해 정한 규칙이다.

1. `script`·`style`·`nav`·`footer`·`header`·`aside`·`figure`·`noscript`를 통째로 지운다.
2. `article` → `[itemprop=articleBody]` → `main` → `body` 순으로 컨테이너를 보고, **문단을
   이어 붙인 길이가 `MIN_BODY_LENGTH`를 처음 넘는 컨테이너에서 멈춘다.**
3. 문단은 `<p>`이고 `MIN_PARAGRAPH_LENGTH`자 미만은 버린다.
4. 그러고도 `MIN_BODY_LENGTH`를 못 넘으면 본문이 없는 것으로 본다.

**넓은 컨테이너를 먼저 보지 않는 이유**가 2번의 순서다. `body`는 언제나 `main`을 포함하므로
"가장 긴 것"을 고르면 화면 문구가 늘 함께 들어온다. 미국 연준 보도자료가 그랬다 —
`body`로 읽으면 "An official website of the United States Government"가 본문 앞에 붙는다.

**최소 길이가 있는 이유**는 반대쪽이다. 금융감독원 게시판은 본문이 첨부에만 있는데 안내
문구 한 문단(40자)이 잡혀서, 하한이 없으면 그 문장이 본문으로 저장된다.

`<li>`는 기본으로 보지 않는다. 메뉴와 관련기사 목록이 함께 들어온다 — NPR에서 재생기
`iframe` 코드가 그렇게 섞였다. 본문을 목록으로 쓰는 출처만 `PARAGRAPH_SELECTORS`로 연다
(백악관 698자 → 5,945자, EIA 3,011자 → 5,232자).

## 첨부와 영상

첨부는 **출처별 규칙이 필요 없었다.** 한국은행·금감원·BOJ 셋 다 파일 확장자나 내려받기
경로를 링크에 그대로 노출한다(`/fileSrc/...hwp`, `fileDown.do`, `data/rev26e11.pdf`).
네이버 리서치만 화면이 JavaScript라 목록·상세와 같은 내부 JSON에서 `attachUrl`을 읽는다.

**첨부와 영상은 위 1번의 정리를 거치지 않은 원본에서 찾는다.** 링크는 본문이 아니라서 화면
장식 안에 있어도 이상하지 않다 — 영상은 `<figure>` 안이 오히려 제자리다.

영상은 내려받지 않고 링크만 남긴다. 찾는 자리 넷을 위에서부터 본다.

1. `<video src>` / `<video><source src>`
2. `iframe[src]` 중 **아는 재생기 호스트만.** 좁히지 않으면 오탐이 난다 — 연합뉴스 기사
   페이지의 첫 `iframe`이 게임 위젯이었다.
3. HTML 안의 `.mp4`·`.m3u8` 주소. CNBC가 여기서 잡힌다.
4. 셋이 다 비었는데 **주소가 영상 경로면 문서 URL 자체를 남긴다.** BBC가 그렇다 —
   `/news/videos/...` 페이지에 `og:video`도 `iframe`도 `<video>`도 `.mp4`도 없고 재생기를
   JavaScript가 만든다. 그래도 그 문서가 영상이라는 사실과 어디로 가면 보는지는 남는다.

## 크기 제한

**본문에 길이 상한을 두지 않고 첨부에 바이트 상한을 두지 않는다**(2026-08-30 사용자 결정).
저장은 원본 보존이고, 프롬프트나 화면에 얼마를 실을지는 읽는 쪽이 정한다. 한 실행이
처리할 **문서 수** 상한은 DAG이 갖는다 — 그건 크기 제한이 아니라 배치 상한이다.
"""

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from pydantic import BaseModel, ConfigDict
from scrapling import Selector

from modules.collectors.document.documents import (
    DocumentPayloadError,
    fetch_url,
    normalize_text,
)
from modules.db import Connection, Cursor
from modules.sql import read_sql

logger = logging.getLogger(__name__)

# 파일을 두는 뿌리. NAS 디렉터리를 이 경로로 마운트한다. DB에는 이 뿌리를 뺀 상대경로만
# 남아서, 마운트 지점이 바뀌어도 행을 고치지 않는다.
DEFAULT_FILE_ROOT = Path("/opt/airflow/files")

# 본문이 아니라 화면 장식이라 통째로 지운다. 태그만 벗기면 CSS와 메뉴가 본문 앞에 붙는다.
#
# **`<form>`은 넣지 않는다.** eGov 게시판은 화면 전체를 `<form>`으로 감싼다 — 한국은행
# 보도자료에서 그것을 지우면 388,802자가 7,136자로 줄어 첨부 링크 8개가 통째로 사라졌다
# (2026-08-30 실측). 검색창 라벨 같은 짧은 조각은 문단 길이 하한이 이미 거른다.
DROP_ELEMENTS = re.compile(
    r"(?is)<(script|style|nav|footer|header|aside|figure|noscript)[^>]*>.*?</\1>",
)

# 본문이 들어 있을 만한 자리를 좁은 것부터 본다. **순서가 규칙이다** — `body`는 언제나
# `main`을 포함하므로 넓은 쪽을 먼저 채택하면 화면 문구가 늘 함께 들어온다.
BODY_CONTAINERS: tuple[str, ...] = ("article", "[itemprop=articleBody]", "main", "body")

# 문단으로 볼 최소 길이. 이보다 짧은 `<p>`는 캡션·라벨·버튼 문구다.
MIN_PARAGRAPH_LENGTH = 40

# 본문으로 인정할 최소 길이. 못 넘으면 본문이 없는 페이지로 본다(목록 화면, 첨부만 있는
# 게시판). 미국 연준의 짧은 보도자료가 이 하한 바로 위에 있어 더 올리지 않는다.
MIN_BODY_LENGTH = 200

# 본문을 문단이 아니라 목록으로 쓰는 출처. 기본값에 `li`를 넣으면 메뉴가 함께 들어오므로
# 실측으로 확인한 곳만 연다(2026-08-30).
PARAGRAPH_SELECTORS: dict[str, str] = {
    "whitehouse": "p, li",
    "eia": "p, li",
}

# 문서별 주소가 없어 본문을 받을 수 없는 출처. KRX 보도자료 화면은 내용 조회도 POST라
# `canonical_url`부터가 목록 화면에 접수번호를 붙인 합성 URL이다. 요청해 봐야 목록이 온다.
UNAVAILABLE_SOURCES: frozenset[str] = frozenset({"krx"})

# 네이버 리서치는 화면이 JavaScript다. 목록·상세와 같은 내부 JSON에서 첨부를 읽는다.
NAVER_RESEARCH_PREFIX = "naver_research_"

# 첨부로 볼 파일 확장자. 경로 끝만 본다 — 질의 문자열에 `.pdf`가 들어간 뷰어 링크
# (한국은행의 `viewer.html?file=….pdf`)를 파일로 세지 않기 위해서다.
ATTACHMENT_SUFFIXES: tuple[str, ...] = (
    ".pdf",
    ".hwp",
    ".hwpx",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".csv",
    ".zip",
)

# 확장자를 안 드러내고 내려받기를 시키는 경로. 금감원은 `fileDown.do`다.
ATTACHMENT_ENDPOINTS = re.compile(r"(?i)(filedown|file_down|fileSrc|/download)")

# 영상 재생기를 싣는 호스트. **아는 것만 본다** — 아무 `iframe`이나 집으면 기사에 붙은
# 게임·광고 위젯이 영상으로 저장된다.
PLAYER_HOSTS: tuple[str, ...] = (
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "vimeo.com",
    "dailymotion.com",
    "player.twitch.tv",
)

# HTML 본문에 그대로 실린 재생 주소.
MEDIA_URL = re.compile(r"https?://[^\s\"'<>]+\.(?:mp4|m3u8)(?:\?[^\s\"'<>]*)?")

# 주소만으로 영상 문서임을 알 수 있는 경로. 재생기를 JavaScript가 만드는 페이지의 마지막
# 단서다(BBC `/news/videos/...`).
VIDEO_PATH = re.compile(r"(?i)/(videos?|tv)/")

PENDING_BODIES = read_sql("postgres", "document", "select_pending_body.sql")
BODY_UPDATE = read_sql("postgres", "document", "update_body.sql")
ATTACHMENT_UPSERT = read_sql("postgres", "document_attachment", "upsert.sql")


class BodyCandidate(BaseModel):
    """본문을 아직 안 받아 본 문서 하나. 큐가 주는 것이 이 모양이다."""

    model_config = ConfigDict(frozen=True)

    id: int
    source_slug: str
    canonical_url: str


class Attachment(BaseModel):
    """문서에 붙은 파일 하나 또는 영상 링크 하나.

    `storage_path`가 있으면 내려받아 저장한 파일이고, 없으면 링크만 남긴 영상이다.
    """

    model_config = ConfigDict(frozen=True)

    position: int
    kind: str
    url: str
    storage_path: str | None = None
    filename: str | None = None
    media_type: str | None = None
    byte_size: int | None = None
    sha256: str | None = None
    fetched_at: datetime | None = None


class DocumentBody(BaseModel):
    """문서 하나에서 **페이지 한 번으로 알아낸 것.**

    첨부 파일은 주소만 담는다. 내려받기는 요청이 더 드는 별개의 일이고, **그 실패가 어렵게
    받은 본문을 되돌리면 안 되기 때문이다.** 본문을 먼저 커밋하고 파일은 하나씩 뒤따른다.

    `status`가 첨부를 받기 전에 이미 정해지는 것도 같은 이유다. 첨부가 있다는 사실은 주소가
    말해 주지 파일이 말해 주지 않는다.
    """

    model_config = ConfigDict(frozen=True)

    document_id: int
    status: str
    body: str | None = None
    file_urls: tuple[str, ...] = ()
    video_urls: tuple[str, ...] = ()


def paragraph_selector(source_slug: str) -> str:
    """그 출처의 문단 선택자. 기본은 `<p>`뿐이다."""
    return PARAGRAPH_SELECTORS.get(source_slug, "p")


def detail_url(source_slug: str, canonical_url: str) -> str:
    """본문을 읽을 주소. 화면 주소와 다른 출처만 바꾼다.

    네이버 리서치의 화면(`/research/economy/13742`)은 JavaScript가 그리고, 그것을 그리는
    내부 JSON이 `/api/research/economy/13742`다. 목록·상세 수집이 쓰는 주소와 같다.
    """
    if source_slug.startswith(NAVER_RESEARCH_PREFIX):
        return canonical_url.replace("/research/", "/api/research/", 1)
    return canonical_url


def clean_markup(raw: str) -> str:
    """본문이 아닌 요소를 통째로 지운다. **본문을 뽑을 때만 쓴다.**

    첨부와 영상은 이 정리를 거치지 않은 원본에서 찾는다. 링크는 본문이 아니라서 화면 장식
    안에 얼마든지 있을 수 있다 — 영상은 `<figure>` 안이 오히려 제자리다.
    """
    return DROP_ELEMENTS.sub(" ", raw)


def extract_body(raw: str, selector: str = "p") -> str | None:
    """문단을 이어 붙인 본문. 없으면 `None`이다.

    좁은 컨테이너부터 보고 **하한을 처음 넘는 곳에서 멈춘다.** 가장 긴 것을 고르면 `body`가
    언제나 이겨서 화면 문구가 함께 들어온다.
    """
    document = Selector(content=clean_markup(raw))
    for container in BODY_CONTAINERS:
        for root in document.css(container):
            paragraphs = []
            for element in root.css(selector):
                text = normalize_text(element.get_all_text())
                if text and len(text) >= MIN_PARAGRAPH_LENGTH:
                    paragraphs.append(text)
            joined = " ".join(paragraphs)
            if len(joined) >= MIN_BODY_LENGTH:
                return joined
    return None


def find_attachment_urls(raw: str, base_url: str) -> tuple[str, ...]:
    """내려받을 첨부의 절대 URL. 순서는 페이지에 나온 차례이고 중복은 뗀다.

    같은 파일이 이름 링크와 `다운로드` 링크로 두 번 나오는 게시판이 흔하다(한국은행·금감원).

    **본문 정리를 거치지 않은 원본에서 찾는다.** 첨부는 본문이 아니라서 화면 장식 안에
    있어도 이상하지 않다.
    """
    document = Selector(content=raw)
    found: list[str] = []
    for anchor in document.css("a"):
        href = (anchor.attrib.get("href") or "").strip()
        if not href:
            continue
        # **경로만 본다.** 질의 문자열까지 보면 한국은행의 뷰어 링크
        # (`viewer.html?file=%2FfileSrc%2F….pdf`)가 파일로 잡힌다.
        path = urlsplit(href).path
        if not path.lower().endswith(ATTACHMENT_SUFFIXES) and not ATTACHMENT_ENDPOINTS.search(path):
            continue
        url = urljoin(base_url, href)
        if url not in found:
            found.append(url)
    return tuple(found)


def naver_attachment_urls(payload: bytes) -> tuple[str, ...]:
    """네이버 리서치 상세 JSON의 첨부 주소. 리포트 원문 PDF 한 건이다.

    응답이 JSON이 아니면 실패시킨다. 경로가 바뀌면 HTML 안내가 200으로 올 수 있고,
    조용히 0건으로 넘기면 리포트 본문이 몇 달째 비어 있어도 알 수 없다.
    """
    try:
        body = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DocumentPayloadError(f"Naver research detail body is not valid JSON: {error}") from None
    if not isinstance(body, dict):
        raise DocumentPayloadError("Naver research detail JSON is not an object")
    content = body.get("researchContent")
    if not isinstance(content, dict):
        raise DocumentPayloadError("Naver research detail JSON has no 'researchContent' object")
    attach_url = content.get("attachUrl")
    return (attach_url,) if isinstance(attach_url, str) and attach_url else ()


def find_video_urls(raw: str, canonical_url: str) -> tuple[str, ...]:
    """영상 링크. 위에서부터 처음 걸리는 자리 하나만 쓴다.

    자리를 섞으면 같은 영상이 재생기 주소와 페이지 주소로 두 번 저장된다.

    **본문 정리를 거치지 않은 원본에서 찾는다.** 영상은 `<figure>` 안에 있는 것이 오히려
    제자리다.
    """
    document = Selector(content=raw)

    embedded = []
    for element in document.css("video, video source"):
        source = (element.attrib.get("src") or "").strip()
        if source:
            embedded.append(urljoin(canonical_url, source))
    if embedded:
        return tuple(dict.fromkeys(embedded))

    players = []
    for frame in document.css("iframe"):
        source = (frame.attrib.get("src") or "").strip()
        if not source:
            continue
        host = urlsplit(urljoin(canonical_url, source)).netloc.lower()
        if any(host == player or host.endswith(f".{player}") for player in PLAYER_HOSTS):
            players.append(urljoin(canonical_url, source))
    if players:
        return tuple(dict.fromkeys(players))

    media = MEDIA_URL.findall(raw)
    if media:
        return tuple(dict.fromkeys(media))

    if VIDEO_PATH.search(urlsplit(canonical_url).path):
        # 재생기를 JavaScript가 만드는 페이지다. 주소 말고는 남길 것이 없지만, 그 문서가
        # 영상이라는 사실 자체가 읽는 쪽에 필요하다.
        return (canonical_url,)
    return ()


def pending_bodies(connection: Connection, limit: int) -> tuple[BodyCandidate, ...]:
    """본문을 아직 안 받아 본 문서. 이 목록이 곧 백필이다."""
    with connection.cursor() as cursor:
        cursor.execute(PENDING_BODIES, (limit,))
        rows = cursor.fetchall()
    return tuple(BodyCandidate(id=row[0], source_slug=row[1], canonical_url=row[2]) for row in rows)


class DocumentBodyCollector:
    """문서 본문과 첨부 수집기. 파일을 두는 뿌리를 들고 돈다.

    뿌리는 실행 동안 안 변하고 메서드마다 다시 들어갈 값이라 생성자가 받는다. 문서는
    호출마다 바뀌므로 메서드 인자다.
    """

    def __init__(self, file_root: Path = DEFAULT_FILE_ROOT) -> None:
        self._file_root = file_root

    @property
    def file_root(self) -> Path:
        return self._file_root

    def collect(self, candidate: BodyCandidate) -> DocumentBody:
        """문서 페이지를 한 번 받아 본문과 첨부 주소를 알아낸다. **파일은 아직 안 받는다.**

        **HTTP 오류를 삼키지 않는다.** 4xx는 `DocumentHTTPError`로 올려 DAG이 그 문서를
        `unavailable`로 확정하고, 연결 실패는 `ConnectionError` 그대로 올려 다음 실행이
        다시 집게 한다.
        """
        if candidate.source_slug in UNAVAILABLE_SOURCES:
            # 요청해 봐야 목록 화면이 온다. 받아 보지 않고 확정한다.
            return DocumentBody(document_id=candidate.id, status="unavailable")

        response = fetch_url(candidate.source_slug, detail_url(candidate.source_slug, candidate.canonical_url))
        raw = response.body.decode("utf-8", errors="replace")

        if candidate.source_slug.startswith(NAVER_RESEARCH_PREFIX):
            # 화면이 JavaScript라 본문이 없다. 리포트 원문은 첨부 PDF다.
            body = None
            file_urls = naver_attachment_urls(response.body)
            video_urls: tuple[str, ...] = ()
        else:
            body = extract_body(raw, paragraph_selector(candidate.source_slug))
            file_urls = find_attachment_urls(raw, candidate.canonical_url)
            video_urls = find_video_urls(raw, candidate.canonical_url)

        return DocumentBody(
            document_id=candidate.id,
            status=_status(body, file_urls),
            body=body,
            file_urls=file_urls,
            video_urls=video_urls,
        )

    def download(self, url: str, position: int, now: datetime | None = None) -> Attachment:
        """첨부 하나를 받아 파일로 두고 그 자리를 돌려준다. **크기 상한은 없다.**

        경로는 내용 해시로 만든다. 같은 파일을 두 문서가 가리키면 한 벌만 남고, 같은 문서를
        다시 집어도 덮어쓸 뿐 파일이 늘지 않는다.
        """
        fetched_at = now or datetime.now(UTC)
        response = fetch_url("attachment", url)
        payload = bytes(response.body)
        digest = hashlib.sha256(payload).hexdigest()
        media_type = _header(response, "content-type")
        filename = _filename(response, url)

        relative = Path("documents") / digest[:2] / f"{digest}{_suffix(filename, media_type)}"
        destination = self._file_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)

        return Attachment(
            position=position,
            kind="file",
            url=url,
            storage_path=str(relative),
            filename=filename,
            media_type=media_type,
            byte_size=len(payload),
            sha256=digest,
            fetched_at=fetched_at,
        )

    @staticmethod
    def store_body(connection: Connection, result: DocumentBody) -> int:
        """본문과 영상 링크를 저장하고 갱신한 문서 수를 돌려준다(0 또는 1).

        영상은 내려받지 않으므로 여기서 끝난다. 파일은 `store_attachment`가 하나씩 받는다.
        커밋은 부르는 쪽이 한다.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                BODY_UPDATE,
                {"body": result.body, "body_status": result.status, "document_id": result.document_id},
            )
            updated = cursor.rowcount
            for offset, url in enumerate(result.video_urls):
                _upsert_attachment(
                    cursor,
                    result.document_id,
                    # 파일이 앞자리를 쓴다. 파일 저장이 뒤에 와도 순서는 페이지에 나온 차례다.
                    Attachment(position=len(result.file_urls) + offset, kind="video", url=url),
                )
        return max(updated, 0)

    @staticmethod
    def store_attachment(connection: Connection, document_id: int, attachment: Attachment) -> None:
        """받아 둔 파일 하나를 문서에 붙인다. 파일 하나가 트랜잭션 하나다."""
        with connection.cursor() as cursor:
            _upsert_attachment(cursor, document_id, attachment)


def _upsert_attachment(cursor: Cursor, document_id: int, attachment: Attachment) -> None:
    cursor.execute(
        ATTACHMENT_UPSERT,
        (
            document_id,
            attachment.position,
            attachment.kind,
            attachment.url,
            attachment.storage_path,
            attachment.filename,
            attachment.media_type,
            attachment.byte_size,
            attachment.sha256,
            attachment.fetched_at,
        ),
    )


def _status(body: str | None, file_urls: tuple[str, ...]) -> str:
    """무엇을 얻었는지로 상태를 정한다.

    본문이 없는데 첨부 파일이 있으면 `attachment_only`다 — 한국은행·금감원·BOJ·네이버
    리서치가 그 자리다. 둘 다 없으면 `empty`이고, 그건 실패가 아니라 "이 페이지에는 글이
    없다"다.

    **파일을 내려받기 전에 정한다.** 첨부가 있다는 사실은 주소가 말해 주고, 내려받기가
    실패해도 그 문서의 본문이 어디 있는지는 달라지지 않는다.
    """
    if body:
        return "ok"
    if file_urls:
        return "attachment_only"
    return "empty"


def _header(response: object, name: str) -> str | None:
    headers = getattr(response, "headers", None) or {}
    for key, value in headers.items():
        if key.lower() == name:
            return str(value).split(";", 1)[0].strip() or None
    return None


def _filename(response: object, url: str) -> str | None:
    """제공처가 준 파일 이름. `Content-Disposition`을 먼저 보고 없으면 주소 끝을 쓴다."""
    disposition = _raw_header(response, "content-disposition")
    if disposition:
        match = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", disposition)
        if match:
            return match.group(1).strip() or None
    tail = Path(urlsplit(url).path).name
    return tail or None


def _raw_header(response: object, name: str) -> str | None:
    headers = getattr(response, "headers", None) or {}
    for key, value in headers.items():
        if key.lower() == name:
            return str(value)
    return None


def _suffix(filename: str | None, media_type: str | None) -> str:
    """저장 파일에 붙일 확장자. 모르면 붙이지 않는다."""
    if filename:
        suffix = Path(filename).suffix.lower()
        if suffix in ATTACHMENT_SUFFIXES:
            return suffix
    if media_type == "application/pdf":
        return ".pdf"
    return ""

