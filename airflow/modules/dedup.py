"""같은 기사를 `canonical_document_id`에 연결한다.

`docs/economic-document-archive-design.md` §6.4의 ①②를 구현한다. 판정은 둘이다.

- **본문 해시(②)**: `content_hash`가 같으면 출처가 달라도 같은 문서다. 해시는 제목·요약·
  본문을 이어 붙인 SHA-256이라 값이 같으면 셋이 글자 그대로 같다. 제목을 보지 않는다.
- **제목 유사도(①의 연장)**: 같은 출처에서 발행 시각이 가까운 문서의 제목을 정규화해
  비교하고 임계를 넘으면 같은 기사로 본다.

어느 쪽이든 본문이 가장 긴 문서를 대표로 삼아 나머지를 연결한다. 연결된 문서는 LLM 평가와
브리핑에서 빠진다. 삭제는 하지 않는다 — 오판이면 컬럼을 NULL로 되돌리면 끝이다.

해시 판정에도 시각 창(±72시간)이 있다. 이유는 실측이다(2026-08-25 운영 DB, 문서 2,332건):
해시가 겹치는 48묶음 중 출처가 둘 이상인 것은 0이었고, 창 없이 걸면 33일에 걸친 BOJ 통계
항목 4건(요약·본문이 비어 제목만 같다)과 6일 간격 KIND 공시 2건이 한 문서로 묶인다.
전재는 시간 단위 안에서 일어나므로 72시간이면 덮는다. 창은 조회 SQL이 갖는다.

# ponytail: stdlib difflib 제목 비교 + 해시 완전일치. 회색지대(같은 사건인데 글자가 다른
# 문서) 판정이 필요해지면 §6.4 ③의 pgvector+BM25 하이브리드로 승격한다.

대표를 "먼저 발행된 쪽"이 아니라 "본문이 긴 쪽"으로 두는 이유: 속보 스텁이 대표가 되면
본문이 있는 본기사가 평가·브리핑에서 빠져, LLM이 한 줄짜리 스텁만 읽게 된다.

임계 `TITLE_SIMILARITY_THRESHOLD`와 창 12시간은 초깃값이다. 판정마다 유사도를 INFO 로그로
남기니 분포를 보고 조정한다. 창이 12시간인 이유는 속보→본기사 간격(분~시간)은 덮고
`[표] 오늘의 환율` 같은 매일 반복 기사(24시간 간격)는 오판하지 않기 위해서다.
"""

import logging
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Protocol, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict

from modules.sql import read_sql

logger = logging.getLogger(__name__)

TITLE_SIMILARITY_THRESHOLD = 0.75
# 정규화 후 이보다 짧은 제목은 완전일치만 중복으로 본다. "Virginia"와 "West Virginia"처럼
# 짧은 제목은 몇 글자 차이로도 유사도가 임계를 넘는다(운영 실측 2026-08-19).
MIN_FUZZY_LENGTH = 10
DEFAULT_DEDUP_BATCH = 200

PENDING_DEDUP = read_sql("postgres", "document", "select_dedup_pending.sql")
DEDUP_CANDIDATES = read_sql("postgres", "document", "select_dedup_candidates.sql")
UPDATE_CANONICAL = read_sql("postgres", "document", "update_canonical.sql")

# [속보], (종합), 【단독】 같은 말머리·꼬리표. 내용이 15자를 넘는 괄호는 제목의 일부로 본다.
_BRACKET_TAG = r"[\[\(【][^\[\]\(\)【】]{1,15}[\]\)】]"
_LEADING_TAGS = re.compile(rf"^(?:\s*{_BRACKET_TAG})+")
_TRAILING_TAGS = re.compile(rf"(?:{_BRACKET_TAG}\s*)+$")
# 물결 표기가 기사마다 갈린다(∼ U+223C, ～ U+FF5E, 〜 U+301C).
_TILDES = str.maketrans({"∼": "~", "～": "~", "〜": "~"})


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> object: ...

    def execute(self, statement: str, parameters: tuple = ()) -> object: ...

    def fetchall(self) -> list[tuple]: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...

    def commit(self) -> None: ...


class DedupDocument(BaseModel):
    """중복 판정에 필요한 문서 조각. `content_length`는 요약+본문 길이다."""

    model_config = ConfigDict(frozen=True)

    id: int
    source_slug: str
    title: str
    published_at: AwareDatetime | None
    detected_at: AwareDatetime
    content_length: int
    content_hash: str
    canonical_document_id: int | None = None


class DedupOutcome(BaseModel):
    """한 번 실행의 판정 집계. DAG 로그와 반환값에 쓴다."""

    model_config = ConfigDict(frozen=True)

    checked: int
    linked: int


def normalize_title(title: str) -> str:
    """말머리·물결·공백 차이를 걷어낸 비교용 제목."""
    text = _LEADING_TAGS.sub("", title)
    text = _TRAILING_TAGS.sub("", text)
    text = text.translate(_TILDES)
    return re.sub(r"\s+", " ", text).strip().casefold()


def title_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_title(a), normalize_title(b)).ratio()


def titles_duplicate(a: str, b: str) -> bool:
    """두 제목이 같은 기사인가. 유사도 임계에 운영 실측(2026-08-19) 오판 가드 셋을 더한다.

    1. 짧은 제목은 완전일치만 받는다 — "Virginia" vs "West Virginia".
    2. 숫자열이 다르면 다른 기사다 — "[연합뉴스 이 시각 헤드라인] - 07:30" vs "- 10:30".
    3. 양쪽 모두에 상대에 없는 낱말이 있으면 다른 기사다 — "[표] 유가증권시장 …" vs
       "[표] 코스닥시장 …", "GDP by Industry" vs "GDP by State". 스텁 제목은 본기사 제목의
       부분집합이라, 진짜 중복은 낱말이 한쪽에만 더 붙는다.
    """
    left = normalize_title(a)
    right = normalize_title(b)
    if left == right:
        return True
    if len(left) < MIN_FUZZY_LENGTH or len(right) < MIN_FUZZY_LENGTH:
        return False
    if re.findall(r"\d+", left) != re.findall(r"\d+", right):
        return False
    left_words = set(re.findall(r"[^\W\d_]+", left))
    right_words = set(re.findall(r"[^\W\d_]+", right))
    if (left_words - right_words) and (right_words - left_words):
        return False
    return SequenceMatcher(None, left, right).ratio() >= TITLE_SIMILARITY_THRESHOLD


def _anchor(document: DedupDocument) -> datetime:
    return document.published_at or document.detected_at


def _duplicates(document: DedupDocument, candidate: DedupDocument) -> bool:
    """같은 문서인가. 해시가 같으면 제목을 보지 않는다(설계 §6.4 ②).

    해시는 제목·요약·본문을 이어 붙인 SHA-256이라 값이 같으면 셋이 글자 그대로 같다.
    전재 기사는 제목이 갈릴 수 있어 제목 유사도만으로는 잡히지 않는다. 반대로 해시 판정에
    시각·출처 조건을 다시 걸지 않는다 — 그 좁히기는 조회 SQL이 이미 했다.
    """
    if document.content_hash == candidate.content_hash:
        return True
    return titles_duplicate(document.title, candidate.title)


def resolve_links(
    document: DedupDocument,
    candidates: tuple[DedupDocument, ...],
) -> tuple[tuple[int, int], ...]:
    """(문서 id, 대표 id) 갱신 목록. 순수 함수라 저장과 분리해 검증한다.

    대표는 무리에서 본문이 가장 긴 문서, 동률이면 늦게 발행된 쪽이다. 대표가 이미 다른
    대표를 가리키면 그 root를 저장해 체인을 만들지 않는다. 무리 안을 가리키던 기존 연결도
    새 root로 옮기되, 무리 밖에 연결된 문서는 건드리지 않는다 — 오판을 밖으로 번지게 하지
    않기 위해서다.
    """
    matches = [candidate for candidate in candidates if _duplicates(document, candidate)]
    if not matches:
        return ()

    pool = [document, *matches]
    pool_ids = {member.id for member in pool}
    representative = max(pool, key=lambda member: (member.content_length, _anchor(member), member.id))
    root = representative.canonical_document_id or representative.id

    links = tuple(
        (member.id, root)
        for member in sorted(pool, key=lambda member: member.id)
        if member.id != root
        and member.canonical_document_id != root
        and (member.canonical_document_id is None or member.canonical_document_id in pool_ids)
    )
    for member_id, target in links:
        logger.info("document %s -> canonical %s (pool=%s)", member_id, target, sorted(pool_ids))
    return links


def pending_dedup(connection: Connection, limit: int = DEFAULT_DEDUP_BATCH) -> tuple[DedupDocument, ...]:
    """평가 전이고 아직 연결되지 않은 문서. 평가가 붙기 전에 판정을 끝내야 한다."""
    with connection.cursor() as cursor:
        cursor.execute(PENDING_DEDUP, (limit,))
        rows = cursor.fetchall()
    return tuple(
        DedupDocument(
            id=row[0],
            source_slug=row[1],
            title=row[2],
            published_at=row[3],
            detected_at=row[4],
            content_length=row[5],
            content_hash=row[6],
        )
        for row in rows
    )


def find_candidates(connection: Connection, document: DedupDocument) -> tuple[DedupDocument, ...]:
    """후보 두 갈래. 같은 출처의 발행 ±12시간 이웃과, 출처와 무관하게 해시가 같은 ±72시간 문서.

    해시 쪽 후보는 출처가 다를 수 있어 `source_slug`도 응답에서 읽는다.
    """
    anchor = _anchor(document)
    with connection.cursor() as cursor:
        cursor.execute(
            DEDUP_CANDIDATES,
            (document.id, document.source_slug, anchor, anchor, document.content_hash, anchor, anchor),
        )
        rows = cursor.fetchall()
    return tuple(
        DedupDocument(
            id=row[0],
            source_slug=row[1],
            title=row[2],
            published_at=row[3],
            detected_at=row[4],
            content_length=row[5],
            canonical_document_id=row[6],
            content_hash=row[7],
        )
        for row in rows
    )


def link_duplicates(connection: Connection, limit: int = DEFAULT_DEDUP_BATCH) -> DedupOutcome:
    """배치 하나를 판정하고 연결한다. 문서 하나가 트랜잭션 하나다(여기서 커밋한다).

    외부 API가 없어 실패는 DB 오류뿐이고, 그건 남은 문서 전부가 똑같이 실패할 문제라
    그대로 올린다. Airflow가 재시도한다.
    """
    documents = pending_dedup(connection, limit)
    linked = 0
    for document in documents:
        links = resolve_links(document, find_candidates(connection, document))
        if not links:
            continue
        with connection.cursor() as cursor:
            for member_id, root in links:
                cursor.execute(UPDATE_CANONICAL, (root, member_id))
        connection.commit()
        linked += len(links)
    return DedupOutcome(checked=len(documents), linked=linked)
