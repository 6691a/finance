"""중복 연결 규칙 검증.

`docs/economic-document-archive-design.md` §6.4의 ①②다. 같은 출처에서 제목만 조금 다른 같은
기사([속보] 스텁 vs 본기사)는 제목 유사도로, 본문 해시가 같은 문서는 제목과 무관하게
`canonical_document_id`로 연결한다.
"""

from datetime import UTC, datetime, timedelta
from typing import Self

import pytest

from modules.dedup import (
    TITLE_SIMILARITY_THRESHOLD,
    DedupDocument,
    link_duplicates,
    normalize_title,
    resolve_links,
    title_similarity,
    titles_duplicate,
)
from modules.sql import read_sql

BASE_AT = datetime(2026, 8, 19, 0, 30, tzinfo=UTC)


def document(
    document_id: int,
    title: str,
    *,
    published_minutes: int | None = 0,
    content_length: int = 100,
    canonical_document_id: int | None = None,
    source_slug: str = "yonhap",
    content_hash: str | None = None,
) -> DedupDocument:
    # 해시 기본값은 문서마다 다르다. 같은 값으로 두면 모든 픽스처가 §6.4 ②로 묶여 제목
    # 판정 테스트가 무엇을 검증하는지 알 수 없게 된다.
    return DedupDocument(
        id=document_id,
        source_slug=source_slug,
        title=title,
        published_at=None if published_minutes is None else BASE_AT + timedelta(minutes=published_minutes),
        detected_at=BASE_AT + timedelta(minutes=published_minutes or 0),
        content_length=content_length,
        content_hash=content_hash or f"hash-{document_id}",
        canonical_document_id=canonical_document_id,
    )


class TestNormalizeTitle:
    def test_strips_leading_headline_tags(self):
        assert normalize_title("[속보] 코스피 5~6%대 급락") == "코스피 5~6%대 급락"

    def test_strips_trailing_headline_tags(self):
        assert normalize_title("코스피 5~6%대 급락(종합)") == "코스피 5~6%대 급락"

    def test_unifies_tilde_variants(self):
        assert normalize_title("코스피 5∼6%대 급락") == normalize_title("코스피 5~6%대 급락")

    def test_collapses_whitespace_and_case(self):
        assert normalize_title("  KOSPI   급락 ") == normalize_title("kospi 급락")


class TestTitleSimilarity:
    def test_the_observed_yonhap_pair_is_a_duplicate(self):
        # 이 작업을 시작하게 한 실제 쌍이다. 이 쌍이 임계를 넘지 못하면 규칙이 무의미하다.
        a = "코스피, 장초반 5∼6%대 급락…매도 사이드카 발동"
        b = "[속보] 코스피 5~6%대 급락…매도 사이드카 발동"
        assert title_similarity(a, b) >= TITLE_SIMILARITY_THRESHOLD

    def test_unrelated_titles_stay_below_the_threshold(self):
        a = "코스피, 장초반 5∼6%대 급락…매도 사이드카 발동"
        b = "원/달러 환율 1,400원 돌파…9개월 만에 최고"
        assert title_similarity(a, b) < TITLE_SIMILARITY_THRESHOLD


class TestTitlesDuplicate:
    """운영 DB 실측(2026-08-19)에서 나온 오판을 가드로 막는다. 같은 틀에 낱말·숫자만
    다른 기사가 유사도만으로는 임계를 넘는다."""

    def test_the_observed_yonhap_pair_passes_the_guards(self):
        a = "코스피, 장초반 5∼6%대 급락…매도 사이드카 발동"
        b = "[속보] 코스피 5~6%대 급락…매도 사이드카 발동"
        assert titles_duplicate(a, b)

    def test_summary_tagged_reissues_are_duplicates(self):
        assert titles_duplicate(
            "통상 사령탑 여한구 산업부 통상교섭본부장 경질", "통상 사령탑 여한구 산업부 통상교섭본부장 경질(종합)"
        )

    def test_identical_short_titles_are_still_duplicates(self):
        assert titles_duplicate("Virginia", "Virginia")

    def test_short_titles_require_an_exact_match(self):
        # 실측 오판: ustr의 서로 다른 주(州) 페이지.
        assert not titles_duplicate("Virginia", "West Virginia")

    def test_different_numbers_are_different_articles(self):
        # 실측 오판: 매시간 나오는 헤드라인 요약. 시각만 다르고 틀은 같다.
        assert not titles_duplicate("[연합뉴스 이 시각 헤드라인] - 07:30", "[연합뉴스 이 시각 헤드라인] - 10:30")

    def test_unique_words_on_both_sides_are_different_articles(self):
        # 실측 오판: 같은 틀의 다른 시장 표, 다른 은행 인가 공지, 다른 통계 보고서.
        assert not titles_duplicate(
            "[표] 유가증권시장 2026년 상반기 연결 영업이익 상·하위 20개사",
            "[표] 코스닥시장 2026년 상반기 연결 영업이익 상·하위 20개사",
        )
        assert not titles_duplicate(
            "Gross Domestic Product by Industry, 2nd Quarter 2023 and Comprehensive Update",
            "Gross Domestic Product by State, 2nd Quarter 2023 and Comprehensive Update",
        )
        assert not titles_duplicate(
            "Federal Reserve Board announces approval of the application by Coastal Bend Bancshares, Inc.",
            "Federal Reserve Board announces approval of the application by FS Bancorp, Inc.",
        )

    def test_two_brokers_reports_with_the_same_title_are_different_documents(self):
        # 네이버 리서치는 증권사를 제목 끝에 낱말로 붙인다. 같은 날(둘 다 KST 자정) 두 증권사가
        # 같은 제목을 내도 그 낱말이 양쪽에 하나씩 남아 다른 문서로 판정된다.
        # 대괄호 말머리(`[대신증권]`)로 붙였다면 벗겨져서 중복으로 묶였을 것이다.
        assert not titles_duplicate(
            "삼성전자: 3Q26 프리뷰, HBM 출하 정상화 - 대신증권",
            "삼성전자: 3Q26 프리뷰, HBM 출하 정상화 - 키움증권",
        )
        assert titles_duplicate(
            "[대신증권] 삼성전자: 3Q26 프리뷰, HBM 출하 정상화",
            "[키움증권] 삼성전자: 3Q26 프리뷰, HBM 출하 정상화",
        )

    def test_extra_words_on_one_side_only_stay_duplicates(self):
        # 스텁 제목은 본기사 제목의 부분집합인 경우가 대부분이다. 한쪽에만 낱말이 더 있는
        # 것은 중복이다.
        assert titles_duplicate(
            "최근 코스피 꾸준한 반등에도…거래대금·거래량 연중 최저수준",
            "최근 코스피 반등에도…거래대금·거래량 연중 최저수준(종합)",
        )


class TestResolveLinks:
    def test_links_the_stub_to_the_fuller_article(self):
        # 본문이 긴 쪽이 대표다. 속보 스텁이 대표가 되면 본기사가 평가·브리핑에서 빠진다.
        stub = document(1, "[속보] 코스피 5~6%대 급락…매도 사이드카 발동", content_length=30)
        full = document(2, "코스피, 장초반 5~6%대 급락…매도 사이드카 발동", published_minutes=40, content_length=900)
        assert resolve_links(full, (stub,)) == ((1, 2),)

    def test_links_the_pending_document_when_a_candidate_is_fuller(self):
        stub = document(1, "[속보] 코스피 5~6%대 급락…매도 사이드카 발동", content_length=30)
        full = document(2, "코스피, 장초반 5~6%대 급락…매도 사이드카 발동", published_minutes=40, content_length=900)
        assert resolve_links(stub, (full,)) == ((1, 2),)

    def test_ties_prefer_the_later_published_document(self):
        first = document(1, "코스피 5~6%대 급락…매도 사이드카 발동", published_minutes=0)
        second = document(2, "[속보] 코스피 5~6%대 급락…매도 사이드카 발동", published_minutes=10)
        assert resolve_links(first, (second,)) == ((1, 2),)

    def test_follows_the_candidate_link_to_its_root(self):
        # 후보가 이미 대표를 가리키면 그 root를 저장한다. 체인을 만들지 않는다.
        linked = document(
            1, "[속보] 코스피 5~6%대 급락…매도 사이드카 발동", content_length=900, canonical_document_id=7
        )
        stub = document(2, "코스피 5~6%대 급락…매도 사이드카 발동", published_minutes=5, content_length=30)
        assert resolve_links(stub, (linked,)) == ((2, 7),)

    def test_rewires_a_member_linked_inside_the_pool(self):
        # 1→2로 묶인 뒤 더 긴 3이 오면 2와 함께 1도 3으로 옮긴다. 남겨 두면 체인이 생긴다.
        stub = document(1, "[속보] 코스피 5~6%대 급락…매도 사이드카 발동", content_length=30, canonical_document_id=2)
        first = document(2, "코스피 5~6%대 급락…매도 사이드카 발동", published_minutes=10, content_length=400)
        fuller = document(
            3, "코스피, 장초반 5~6%대 급락…매도 사이드카 발동(종합)", published_minutes=60, content_length=900
        )
        assert resolve_links(fuller, (stub, first)) == ((1, 3), (2, 3))

    def test_does_not_rewire_a_member_linked_outside_the_pool(self):
        # 다른 무리에 이미 연결된 문서를 끌어오지 않는다. 오판을 무리 밖으로 번지게 하지 않는다.
        elsewhere = document(1, "코스피 5~6%대 급락…매도 사이드카 발동", content_length=900, canonical_document_id=99)
        other = document(3, "[속보] 코스피 5~6%대 급락…매도 사이드카 발동", published_minutes=5, content_length=400)
        pending = document(2, "코스피 5~6%대 급락…매도 사이드카 발동(종합)", published_minutes=10, content_length=30)
        links = resolve_links(pending, (elsewhere, other))
        assert all(member != 1 for member, _ in links)
        assert links == ((2, 99), (3, 99))

    def test_links_a_reprint_with_a_different_title_by_hash(self):
        # 전재 기사는 제목이 갈린다. 해시가 같으면 제목·요약·본문이 글자 그대로 같다는 뜻이라
        # 제목 유사도를 보지 않는다(설계 §6.4 ②).
        origin = document(
            1,
            "코스피 5~6%대 급락…매도 사이드카 발동",
            content_length=900,
            source_slug="yonhap",
            content_hash="same",
        )
        reprint = document(
            2,
            "[전재] 유가증권시장 급락 관련",
            published_minutes=30,
            content_length=900,
            source_slug="einfomax",
            content_hash="same",
        )
        # 해시가 같으면 본문 길이도 같아 대표는 늘 동률 규칙(늦게 발행된 쪽)이 정한다. 글자가
        # 똑같은 두 문서라 어느 쪽이 대표든 읽는 쪽이 잃는 것이 없다.
        assert resolve_links(reprint, (origin,)) == ((1, 2),)

    def test_a_different_hash_falls_back_to_the_title_rule(self):
        # 해시가 다르면 예전 규칙 그대로다. 출처가 다른 무관한 기사를 끌어오지 않는다.
        other_source = document(
            1, "원/달러 환율 1,400원 돌파…9개월 만에 최고", source_slug="einfomax", content_length=900
        )
        pending = document(2, "코스피 5~6%대 급락…매도 사이드카 발동", published_minutes=30)
        assert resolve_links(pending, (other_source,)) == ()

    def test_returns_nothing_without_a_similar_candidate(self):
        pending = document(1, "코스피 5~6%대 급락…매도 사이드카 발동")
        unrelated = document(2, "원/달러 환율 1,400원 돌파…9개월 만에 최고", published_minutes=5)
        assert resolve_links(pending, (unrelated,)) == ()


class FakeCursor:
    def __init__(self, results: list[list[tuple]]) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self._results = results

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple = ()) -> None:
        self.calls.append((statement, parameters))

    def fetchall(self) -> list[tuple]:
        return self._results.pop(0) if self._results else []


class FakeConnection:
    def __init__(self, results: list[list[tuple]] | None = None) -> None:
        self.recorded_cursor = FakeCursor(results or [])
        self.commits = 0

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor

    def commit(self) -> None:
        self.commits += 1


def row(doc: DedupDocument) -> tuple:
    return (
        doc.id,
        doc.source_slug,
        doc.title,
        doc.published_at,
        doc.detected_at,
        doc.content_length,
        doc.canonical_document_id,
        doc.content_hash,
    )


def pending_row(doc: DedupDocument) -> tuple:
    return (
        doc.id,
        doc.source_slug,
        doc.title,
        doc.published_at,
        doc.detected_at,
        doc.content_length,
        doc.content_hash,
    )


class TestLinkDuplicates:
    def test_links_and_commits_per_document(self):
        stub = document(1, "[속보] 코스피 5~6%대 급락…매도 사이드카 발동", content_length=30)
        full = document(2, "코스피, 장초반 5~6%대 급락…매도 사이드카 발동", published_minutes=40, content_length=900)
        pending_rows = [pending_row(full)]
        connection = FakeConnection(results=[pending_rows, [row(stub)]])

        outcome = link_duplicates(connection)

        assert outcome.checked == 1
        assert outcome.linked == 1
        assert connection.commits == 1
        update_calls = [call for call in connection.recorded_cursor.calls if "UPDATE document" in call[0]]
        assert update_calls == [(update_calls[0][0], (2, 1))]

    def test_does_not_commit_without_a_link(self):
        pending = document(1, "코스피 5~6%대 급락…매도 사이드카 발동")
        pending_rows = [pending_row(pending)]
        connection = FakeConnection(results=[pending_rows, []])

        outcome = link_duplicates(connection)

        assert outcome.checked == 1
        assert outcome.linked == 0
        assert connection.commits == 0

    def test_passes_the_anchor_source_and_hash_to_the_candidate_query(self):
        pending = document(1, "코스피 5~6%대 급락…매도 사이드카 발동")
        pending_rows = [pending_row(pending)]
        connection = FakeConnection(results=[pending_rows, []])

        link_duplicates(connection)

        candidate_call = connection.recorded_cursor.calls[1]
        # 두 갈래가 같은 기준 시각을 쓴다. 제목 창에 둘, 해시 창에 둘이다.
        assert candidate_call[1] == (
            1,
            "yonhap",
            pending.published_at,
            pending.published_at,
            pending.content_hash,
            pending.published_at,
            pending.published_at,
        )


class TestSqlFiles:
    """조회하는 쪽이 중복을 빼는지 SQL 파일 자체를 고정한다."""

    @pytest.mark.parametrize(
        "filename",
        ["select_pending_assessment.sql", "select_briefing_candidates.sql"],
    )
    def test_consumers_skip_linked_documents(self, filename: str):
        assert "canonical_document_id IS NULL" in read_sql("postgres", "document", filename)

    def test_briefing_summary_backlog_skips_linked_documents(self):
        # 평가를 스킵한 중복이 영원히 backlog로 집계되면 안 된다.
        summary = read_sql("postgres", "document", "select_briefing_summary.sql")
        assert summary.count("canonical_document_id IS NULL") >= 2

    def test_the_candidate_window_is_twelve_hours(self):
        # `[표] 오늘의 환율` 같은 매일 반복 기사(24시간 간격)를 오판하지 않는 창이다.
        candidates = read_sql("postgres", "document", "select_dedup_candidates.sql")
        assert "12 hours" in candidates
        assert "coalesce(published_at, detected_at)" in candidates

    def test_the_hash_branch_keeps_its_own_window(self):
        """해시 갈래에 창이 없으면 33일 간격 BOJ 통계 4건이 한 문서로 묶인다(2026-08-25 실측)."""
        candidates = read_sql("postgres", "document", "select_dedup_candidates.sql")
        assert "content_hash = %s" in candidates
        assert "72 hours" in candidates
        # 해시 갈래는 출처를 걸지 않는다. 전재는 출처가 다른 것이 요점이다.
        assert candidates.count("source_slug = %s") == 1

    def test_pending_carries_the_hash_used_by_the_rule(self):
        pending = read_sql("postgres", "document", "select_dedup_pending.sql")
        assert "content_hash" in pending

    def test_pending_targets_only_unassessed_unlinked_documents(self):
        pending = read_sql("postgres", "document", "select_dedup_pending.sql")
        assert "assessed_at IS NULL" in pending
        assert "canonical_document_id IS NULL" in pending
