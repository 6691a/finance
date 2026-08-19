"""공식기관·언론 피드에서 경제 문서를 발견해 정규화한다.

`docs/economic-document-archive-design.md` 2단계의 수집 절반이다. LLM 태깅은 별도 모듈이
맡는다. **모델 장애가 원문 수집을 막지 않게** 둘을 나눈 것이 설계의 첫 결정이다.

시세 수집기들과 다른 점이 셋이다.

- **수집 대상 목록이 코드가 아니라 DB에 있다.** `document_source` 테이블이 어떤 피드를 어디까지
  가져올지 정한다. 이용조건은 출처마다 다르고 바뀌기 때문에, 한 곳이 본문 자동수집을 막으면
  `collection_mode`를 내리는 것으로 끝나야 한다. 그래서 여기에는 심볼 Enum이 없다.
- **피드 응답 1회가 `source_record` 1건이다.** 응답 하나가 문서 수십 개를 담고 있어 문서마다
  계보를 만들면 레코드가 문서보다 많아진다. `source_type`은 `crawl`, `source`는 출처 slug,
  `source_key`는 `feed`다.
- **본문 해시의 안정성이 이 모듈의 계약이다.** 같은 기사를 두 번 받아 해시가 달라지면 매시간
  같은 문서가 갱신되고, 나중에 붙일 LLM 태깅이 그때마다 다시 돈다. 피드 요약에는 HTML 조각과
  상대시각이 섞여 오므로 `normalize_text`가 그걸 걷어낸 뒤에 해시한다.

RSS와 Atom은 표준 라이브러리로 읽는다. `xml.etree.ElementTree`가 XML을,
`email.utils.parsedate_to_datetime`이 RFC 822 날짜를 처리한다. `feedparser`를 넣을 이유가 없다.

요청은 `urlopen`이 아니라 scrapling `Fetcher`다. 여러 언론사가 기본 파이썬 User-Agent를
막는다. `yahoo.py`와 같은 도구이고 새 의존성은 없다.
"""

import hashlib
import html
import json
import logging
import re
from collections.abc import Sequence
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, Self
from xml.etree import ElementTree

from curl_cffi.curl import CurlError
from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator, model_validator
from scrapling.fetchers import Fetcher

from modules.sql import read_sql
from modules.upsert import execute_upserts

logger = logging.getLogger(__name__)

SOURCE_KEY = "feed"
SOURCE_TYPE = "crawl"

REQUEST_TIMEOUT_SECONDS = 30

# curl_cffi가 흉내 낼 브라우저. 여러 언론사가 기본 파이썬 TLS·헤더 지문을 막는다.
IMPERSONATE = "chrome"

# Atom 네임스페이스. RSS 2.0은 네임스페이스가 없다.
ATOM = "{http://www.w3.org/2005/Atom}"

# 한 피드에서 한 번에 받아들일 최대 항목 수. 피드가 갑자기 수천 건을 실어 보내면 그건
# 우리가 기대한 발견 채널이 아니다. 조용히 다 삼키지 않고 잘렸다는 사실을 남긴다.
MAX_ITEMS_PER_FEED = 500

# 정규화에서 걷어낼 조각. 본문이 아니라 화면 장식이라 남겨 두면 해시가 매번 바뀐다.
#
# **이 목록이 곧 해시 안정성이다.** 출처를 늘릴 때 같은 문서를 두 번 받아 해시가 같은지
# 확인하는 것이 인수 조건이고, 다르면 여기에 규칙을 더한다.
NOISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"<[^>]+>"),  # 피드 요약에 섞여 오는 HTML 조각
    re.compile(r"\d+\s*(?:초|분|시간|일)\s*전"),  # 3분 전
    re.compile(r"\b\d+\s+(?:seconds?|minutes?|hours?|days?)\s+ago\b", re.IGNORECASE),
    re.compile(r"조회수?\s*[\d,]+"),
    re.compile(r"공유하기|카카오톡|페이스북|트위터"),
    re.compile(r"(?:무단\s*전재|재배포\s*금지)[^\n]*"),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),  # 기자 이메일
    re.compile(r"©\s*\S+"),
)

# 추적용 질의 문자열. 같은 문서가 캠페인마다 다른 URL로 와서 `external_id`가 갈린다.
TRACKING_PARAMS = re.compile(r"[?&](?:utm_[^=]+|fbclid|gclid|igshid)=[^&]*")


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Sequence[Any] = ()) -> object: ...

    def executemany(self, statement: str, parameters: Sequence[Sequence[Any]]) -> object: ...

    def fetchone(self) -> Any: ...

    def fetchall(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


class DocumentHTTPError(RuntimeError):
    """피드가 2xx가 아닌 상태로 응답했다. 재시도 가능 여부는 호출자가 `status`로 판단한다."""

    def __init__(self, status: int) -> None:
        super().__init__(f"Feed responded with HTTP {status}")
        self.status = status


class DocumentPayloadError(ValueError):
    """피드 응답이 우리가 아는 모양이 아니다. 재시도해도 같은 결과다."""


class FeedSource(BaseModel):
    """`document_source` 한 행. 수집 정책의 원본은 DB다."""

    model_config = ConfigDict(frozen=True)

    slug: str
    name: str
    source_kind: str
    country: str | None
    language: str
    feed_url: str
    collection_mode: str

    @property
    def document_type(self) -> str:
        """공식기관은 보도자료, 언론은 기사로 둔다.

        피드가 종류를 알려 주지 않아 출처 종류로 정한다. 연설문·보고서를 갈라야 할 만큼
        쌓이면 그때 출처별 규칙을 붙인다.
        """
        return "press_release" if self.source_kind == "official" else "article"


class FeedItem(BaseModel):
    """정규화한 피드 항목 1건."""

    model_config = ConfigDict(frozen=True)

    external_id: str
    canonical_url: str
    title: str
    summary: str | None
    published_at: AwareDatetime | None

    @field_validator("published_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime | None) -> datetime | None:
        return moment.astimezone(UTC) if moment is not None else None


class FeedResponse(BaseModel):
    """피드 한 번의 호출 결과와 그 호출을 재현하는 데 필요한 메타데이터."""

    model_config = ConfigDict(frozen=True)

    slug: str
    url: str
    body: bytes
    status: int
    started_at: AwareDatetime
    completed_at: AwareDatetime

    @field_validator("started_at", "completed_at")
    @classmethod
    def normalize_to_utc(cls, moment: datetime) -> datetime:
        return moment.astimezone(UTC)

    @model_validator(mode="after")
    def require_ordered_span(self) -> Self:
        if self.started_at > self.completed_at:
            raise ValueError("started_at must not be after completed_at")
        return self


class SourceOutcome(BaseModel):
    """한 출처의 수집 결과. 성공이든 실패든 `source_record.metadata`에 그대로 실린다."""

    model_config = ConfigDict(frozen=True)

    slug: str
    feed_url: str
    status: int | None = None
    item_count: int = 0
    truncated: bool = False
    latest_published_at: str | None = None
    error: str | None = None


def normalize_text(raw: str | None) -> str | None:
    """화면 장식을 걷어내고 공백을 하나로 줄인다.

    엔티티를 **먼저** 푼다. 피드는 HTML을 한 번 더 escape해서 싣는 경우가 흔해
    (`&lt;p&gt;`), 태그를 먼저 지우면 그 조각이 그대로 남는다. 푼 뒤에 태그 규칙을 돌리면
    양쪽이 한 번에 걷힌다.

    `html.unescape`를 쓴다. 처음에는 XML 파서로 풀었는데 BEA 요약처럼 escape되지 않은 `<`가
    섞인 본문에서 `ParseError`가 나 수집 태스크가 통째로 죽었다(실측 2026-08-15). 엔티티를
    푸는 데 파서가 필요하지 않다.
    """
    if raw is None:
        return None
    text = html.unescape(raw)
    for pattern in NOISE_PATTERNS:
        text = pattern.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def canonical_url(raw: str) -> str:
    """추적 질의와 fragment를 뗀 URL. `external_id`가 캠페인마다 갈리지 않게 한다."""
    url = raw.strip().split("#", 1)[0]
    url = TRACKING_PARAMS.sub("", url)
    return url.rstrip("?&")


def content_hash(title: str, summary: str | None, body: str | None) -> str:
    """정규화한 제목·요약·본문의 SHA-256.

    구분자를 넣어 이어 붙인다. 안 넣으면 제목 끝과 요약 앞이 붙어 서로 다른 문서가 같은
    해시를 낼 수 있다.
    """
    joined = "\x1f".join(part or "" for part in (title, summary, body))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _text(element: ElementTree.Element | None) -> str | None:
    if element is None:
        return None
    return element.text


def _published_at(raw: str | None) -> datetime | None:
    """RSS의 RFC 822와 Atom의 ISO 8601을 모두 받는다. 모르는 표기는 값 없음으로 둔다.

    발행 시각을 지어내지 않는다. `document.published_at`은 NULL을 허용하고, 우리가 그 문서를
    처음 본 시각은 `detected_at`이 따로 갖는다.
    """
    if not raw:
        return None
    text = raw.strip()
    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError):
        pass
    try:
        # 3.11부터 `fromisoformat`이 `Z`를 그대로 받는다.
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # naive로 오면 시간대를 모른다는 뜻이다. UTC로 단정하지 않고 버린다.
    return parsed if parsed.tzinfo is not None else None


def parse_feed(body: bytes) -> tuple[tuple[FeedItem, ...], bool]:
    """RSS 2.0과 Atom에서 항목을 뽑는다. (항목, 잘렸는지)를 돌려준다.

    **응답이 XML이 아니면 실패시킨다.** 주소가 바뀐 사이트는 404 대신 HTML 안내 페이지를
    200으로 준다. 조용히 0건으로 넘기면 그 출처가 몇 달째 비어 있어도 알 수 없다.
    `boe.py`가 HTML 오류 페이지를 실패로 만드는 것과 같은 취지다.

    항목이 0건인 것은 실패가 아니다. 새 문서가 없는 시간대가 정상이다.
    """
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as error:
        raise DocumentPayloadError(f"Feed body is not valid XML: {error}") from None

    entries = root.findall(".//item") or root.findall(f".//{ATOM}entry")
    truncated = len(entries) > MAX_ITEMS_PER_FEED

    items: list[FeedItem] = []
    for entry in entries[:MAX_ITEMS_PER_FEED]:
        link = _text(entry.find("link"))
        if not link:
            # Atom은 링크를 속성으로 준다.
            atom_link = entry.find(f"{ATOM}link")
            link = atom_link.get("href") if atom_link is not None else None
        title = normalize_text(_text(entry.find("title")) or _text(entry.find(f"{ATOM}title")))
        if not link or not title:
            # 링크나 제목이 없으면 문서로 가리킬 수도, 사람이 읽을 수도 없다.
            continue

        url = canonical_url(link)
        identifier = (_text(entry.find("guid")) or _text(entry.find(f"{ATOM}id")) or url).strip()
        summary = normalize_text(
            _text(entry.find("description"))
            or _text(entry.find(f"{ATOM}summary"))
            or _text(entry.find(f"{ATOM}content"))
        )
        published = _published_at(
            _text(entry.find("pubDate")) or _text(entry.find(f"{ATOM}published")) or _text(entry.find(f"{ATOM}updated"))
        )
        items.append(
            FeedItem(
                external_id=identifier,
                canonical_url=url,
                title=title,
                summary=summary,
                published_at=published,
            )
        )

    return tuple(items), truncated


def fetch_feed(source: FeedSource) -> FeedResponse:
    """피드 한 번을 받아 온다. 파싱은 하지 않는다."""
    started_at = datetime.now(UTC)
    try:
        response = Fetcher.get(source.feed_url, impersonate=IMPERSONATE, timeout=REQUEST_TIMEOUT_SECONDS)
    except CurlError as error:
        # 타임아웃, DNS 실패, TLS 실패는 재시도 가능한 오류로 올린다. URL에 비밀이 없어
        # 원인을 체인으로 남긴다.
        raise ConnectionError(f"Feed request failed for {source.slug}: {error}") from error

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


SOURCE_RECORD_INSERT = read_sql("postgres", "source_record", "insert.sql")
DOCUMENT_UPSERT = read_sql("postgres", "document", "upsert.sql")
ENABLED_SOURCES = read_sql("postgres", "document_source", "select_enabled.sql")


def enabled_sources(connection: Connection) -> tuple[FeedSource, ...]:
    """수집을 켜 둔 출처. 목록의 원본은 DB다."""
    with connection.cursor() as cursor:
        cursor.execute(ENABLED_SOURCES)
        rows = cursor.fetchall()
    return tuple(
        FeedSource(
            slug=row[0],
            name=row[1],
            source_kind=row[2],
            country=row[3],
            language=row[4],
            feed_url=row[5],
            collection_mode=row[6],
        )
        for row in rows
    )


def store_documents(
    connection: Connection,
    source: FeedSource,
    response: FeedResponse,
    items: Sequence[FeedItem],
    truncated: bool = False,
    detected_at: datetime | None = None,
) -> tuple[int, SourceOutcome]:
    """출처 하나의 수집 결과를 저장하고 (저장한 문서 수, 결과)를 돌려준다.

    **출처 하나가 트랜잭션 하나다.** 호출자가 출처마다 commit하므로 한 곳이 실패해도 앞의
    성공이 되돌아가지 않는다.

    **문서가 0건이어도 `source_record`는 남긴다.** 새 문서가 없는 시간대와 아직 조회하지
    않은 시간대가 구분돼야 한다.

    `collection_mode`가 `metadata_only`면 요약을 저장하지 않는다. 본문은 아직 어느 출처도
    받지 않으므로 항상 NULL이다.
    """
    detected_at = detected_at or datetime.now(UTC)
    keep_summary = source.collection_mode != "metadata_only"
    content_level = source.collection_mode if keep_summary else "metadata_only"

    outcome = SourceOutcome(
        slug=source.slug,
        feed_url=source.feed_url,
        status=response.status,
        item_count=len(items),
        truncated=truncated,
        latest_published_at=max(
            (item.published_at.isoformat() for item in items if item.published_at is not None),
            default=None,
        ),
    )
    metadata = json.dumps({"feed_url": source.feed_url, "outcome": outcome.model_dump()}, ensure_ascii=False)

    with connection.cursor() as cursor:
        cursor.execute(
            SOURCE_RECORD_INSERT,
            (
                SOURCE_TYPE,
                source.slug,
                SOURCE_KEY,
                response.started_at,
                response.completed_at,
                "succeeded",
                len(items),
                # 원본은 남기지 않는다. `payload` 컬럼이 jsonb인데 피드는 XML이다.
                None,
                metadata,
            ),
        )
        source_record_id = cursor.fetchone()[0]
        execute_upserts(
            cursor,
            DOCUMENT_UPSERT,
            [
                (
                    source.slug,
                    item.external_id,
                    item.canonical_url,
                    source.document_type,
                    item.title,
                    item.summary if keep_summary else None,
                    # 본문 수집은 아직 범위 밖이다. 열리면 여기만 채운다.
                    None,
                    source.language,
                    item.published_at,
                    detected_at,
                    content_level,
                    content_hash(item.title, item.summary if keep_summary else None, None),
                    source_record_id,
                )
                for item in items
            ],
        )
    return len(items), outcome
