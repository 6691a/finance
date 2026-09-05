"""원인 분석의 순수한 부분 — 질의 조립과 근거 검증. **모델도 DB도 안 부른다.**"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from modules.shock.domain import (
    MAX_CAUSE_CHARS,
    CauseAnswer,
    CauseInput,
    CauseKind,
    Direction,
    DocumentRow,
    PeerMove,
    PeerRegion,
    SearchRow,
)
from modules.shock.search import SearchHit, build_queries, collect

DETECTED = datetime(2026, 9, 3, 5, 16, tzinfo=UTC)  # KST 14:16


def peer(symbol: str, change: str | None, region: PeerRegion = PeerRegion.ASIA) -> PeerMove:
    if change is None:
        return PeerMove(symbol=symbol, label=symbol, region=region, bars=0)
    return PeerMove(
        symbol=symbol, label=symbol, region=region, change_pct=Decimal(change), bars=30, available=True
    )


# --- 질의 조립 ------------------------------------------------------------------


def test_queries_use_kst_and_never_leak_the_answer():
    """**원인을 모르는 상태에서 만들 수 있는 것만.** "엔캐리" 같은 낱말이 들어가면 안 된다."""
    queries = build_queries(detected_at=DETECTED, direction=Direction.DROP, peers=[])

    assert queries == [
        "코스피 9월 3일 급락 이유",
        "코스피 2026년 9월 3일 오후 2시 급락 원인",
    ]
    for banned in ("엔캐리", "엔 캐리", "일본은행", "BOJ"):
        assert all(banned not in query for query in queries)


def test_the_asia_query_appears_only_when_peers_moved_together():
    """세 번째 질의는 §4의 동시성 결과가 있어야 만들어진다."""
    alone = build_queries(
        detected_at=DETECTED,
        direction=Direction.DROP,
        peers=[peer("NIKKEI225", "0.4"), peer("HSI", None)],
    )
    together = build_queries(
        detected_at=DETECTED,
        direction=Direction.DROP,
        peers=[peer("NIKKEI225", "-0.5"), peer("SSE_COMP", "-0.6"), peer("HSI", None)],
    )

    assert len(alone) == 2
    assert together[2] == "한국 일본 중국 증시 동시 급락 9월 3일"


def test_us_futures_alone_do_not_make_the_asia_query():
    """미국 선물이 같이 움직인 것은 "한중일 동시"가 아니라 글로벌이다.

    그 질의를 던지면 없는 사실을 질의가 주장한다.
    """
    queries = build_queries(
        detected_at=DETECTED,
        direction=Direction.DROP,
        peers=[
            peer("SP500_FUT", "-0.4", PeerRegion.US),
            peer("NASDAQ100_FUT", "-0.6", PeerRegion.US),
            peer("NIKKEI225", "0.3"),
        ],
    )

    assert len(queries) == 2


def test_a_surge_asks_about_a_surge():
    queries = build_queries(
        detected_at=DETECTED,
        direction=Direction.SURGE,
        peers=[peer("NIKKEI225", "0.5"), peer("SSE_COMP", "0.6")],
    )

    assert "급등" in queries[0]
    assert queries[2] == "한국 일본 중국 증시 동시 급등 9월 3일"


# --- 검색 결과 합치기 -------------------------------------------------------------


class FakeSearch:
    def __init__(self, by_query: dict[str, list[SearchHit]]) -> None:
        self._by_query = by_query
        self.asked: list[str] = []

    def search(self, query: str, **_: object) -> list[SearchHit]:
        self.asked.append(query)
        return self._by_query.get(query, [])


def hit(url: str, *, query: str = "q", rank: int = 1, published: datetime | None = None) -> SearchHit:
    return SearchHit(
        query=query,
        rank=rank,
        title=f"제목 {url}",
        url=url,
        publisher="example.com",
        snippet="발췌",
        published_at=published,
    )


def test_articles_published_before_the_shock_are_dropped():
    """재료는 전부터 있다. 그것을 근거로 받으면 그날 그 시각의 방아쇠로 둔갑한다."""
    client = FakeSearch(
        {
            "q": [
                hit("https://a", published=DETECTED - timedelta(days=1)),
                hit("https://b", published=DETECTED + timedelta(hours=2)),
            ]
        }
    )

    hits = collect(client, ["q"], published_after=DETECTED)

    assert [row.url for row in hits] == ["https://b"]


def test_an_article_without_a_published_date_is_kept():
    """버리면 "날짜를 못 준 것"과 "오래된 것"이 같아진다. 판단은 모델이 발췌를 보고 한다."""
    client = FakeSearch({"q": [hit("https://a", published=None)]})

    assert len(collect(client, ["q"], published_after=DETECTED)) == 1


def test_the_same_url_from_two_queries_is_kept_once():
    later = DETECTED + timedelta(hours=1)
    client = FakeSearch(
        {
            "q1": [hit("https://a", query="q1", rank=1, published=later)],
            "q2": [hit("https://a", query="q2", rank=5, published=later)],
        }
    )

    hits = collect(client, ["q1", "q2"], published_after=DETECTED)

    assert len(hits) == 1
    # 처음 본 질의·순위가 남는다. 나중 시도가 덮어쓰면 "언제 처음 봤나"가 사라진다.
    assert hits[0].query == "q1"
    assert hits[0].rank == 1


# --- 근거 검증 ------------------------------------------------------------------


def document(doc_id: int) -> DocumentRow:
    return DocumentRow(
        id=doc_id,
        published_at=DETECTED + timedelta(hours=1),
        source_slug="einfomax",
        title="제목",
    )


def payload(*, documents: tuple[DocumentRow, ...] = (), searches: int = 0) -> CauseInput:
    return CauseInput(
        shock_event_id=1,
        symbol="KOSPI",
        direction=Direction.DROP,
        detected_at=DETECTED,
        extreme_at=DETECTED - timedelta(minutes=29),
        extreme_price=Decimal("6661.04"),
        trigger_price=Decimal("6527.70"),
        move_pct=Decimal("-2.0018"),
        attempt=1,
        as_of_at=DETECTED + timedelta(days=1),
        documents=documents,
        search_hits=tuple(
            SearchRow(index=index, title="t", url=f"https://s{index}", publisher="p", snippet="s")
            for index in range(1, searches + 1)
        ),
    )


def verify(builder_payload: CauseInput, answer: CauseAnswer):
    """`ShockCauseBuilder._verify`를 모델 없이 부른다."""
    from langchain_core.messages import AIMessage

    from modules.shock.cause import ShockCauseBuilder

    builder = ShockCauseBuilder.__new__(ShockCauseBuilder)
    builder._payload = builder_payload
    return builder._verify(AIMessage(content=answer.model_dump_json()))


def test_an_id_outside_the_given_list_is_dropped():
    given = payload(documents=(document(1), document(2)))
    answer = CauseAnswer(found=True, cause_text="원인", cause_kind=CauseKind.RUMOR, document_ids=(1, 999))

    verified, rejected = verify(given, answer)

    assert verified is not None
    assert verified.document_ids == (1,)
    assert any("999" in reason for reason in rejected)


def test_a_search_index_outside_the_given_range_is_dropped():
    given = payload(documents=(document(1),), searches=2)
    answer = CauseAnswer(found=True, cause_text="원인", document_ids=(1,), search_indexes=(2, 7))

    verified, rejected = verify(given, answer)

    assert verified is not None
    assert verified.search_indexes == (2,)
    assert any("7" in reason for reason in rejected)


def test_an_answer_with_no_surviving_evidence_is_taken_down():
    """근거가 전부 버려지면 답 전체를 내린다. 지어낸 원인이 저장되는 것보다 낫다."""
    given = payload(documents=(document(1),))
    answer = CauseAnswer(found=True, cause_text="원인", document_ids=(999,))

    verified, rejected = verify(given, answer)

    assert verified is not None
    assert verified.found is False
    assert "근거가 하나도 안 남았다" in rejected


def test_evidence_may_come_from_search_alone():
    """우리 문서가 하나도 없어도 검색만으로 답할 수 있다."""
    given = payload(searches=3)
    answer = CauseAnswer(found=True, cause_text="원인", search_indexes=(1, 3))

    verified, rejected = verify(given, answer)

    assert verified is not None
    assert verified.found is True
    assert verified.search_indexes == (1, 3)
    assert rejected == []


def test_a_long_cause_is_cut_and_the_reason_is_reported():
    given = payload(documents=(document(1),))
    answer = CauseAnswer(found=True, cause_text="가" * (MAX_CAUSE_CHARS + 50), document_ids=(1,))

    verified, rejected = verify(given, answer)

    assert verified is not None
    assert len(verified.cause_text) == MAX_CAUSE_CHARS
    assert any("잘랐다" in reason for reason in rejected)


def test_not_found_is_a_normal_result():
    """장중 급변의 상당수는 아무도 기사로 쓰지 않는다. 그때 지어내는 것보다 낫다."""
    verified, rejected = verify(payload(documents=(document(1),)), CauseAnswer(found=False))

    assert verified is not None
    assert verified.found is False
    assert rejected == []


@pytest.mark.parametrize("kind", list(CauseKind))
def test_every_cause_kind_survives_verification(kind):
    given = payload(documents=(document(1),))
    answer = CauseAnswer(found=True, cause_text="원인", cause_kind=kind, document_ids=(1,))

    verified, _ = verify(given, answer)

    assert verified is not None
    assert verified.cause_kind is kind
