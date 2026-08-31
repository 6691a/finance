import inspect
import json
import re
from datetime import UTC, datetime
from typing import Self

import pytest
from sqlalchemy import Table

from apps.models.content import Document as DocumentModel
from apps.models.raw import SourceRecord
from modules.collectors.document.documents import (
    DOCUMENT_UPSERT,
    EXISTING_EXTERNAL_IDS,
    SOURCE_RECORD_INSERT,
    TAGGABLE_INSTRUMENTS,
    DocumentPayloadError,
    FeedResponse,
    FeedSource,
    canonical_url,
    content_hash,
    existing_external_ids,
    normalize_text,
    parse_feed,
    store_documents,
    taggable_tickers,
)

SOURCE_RECORD_ID = 7
STARTED_AT = datetime(2026, 8, 15, 3, 5, tzinfo=UTC)
COMPLETED_AT = datetime(2026, 8, 15, 3, 5, 2, tzinfo=UTC)
DETECTED_AT = datetime(2026, 8, 15, 3, 5, 3, tzinfo=UTC)

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Example</title>
  <item>
    <title>기준금리 동결</title>
    <link>https://example.com/a?utm_source=rss&amp;utm_medium=feed#top</link>
    <guid>urn:example:a</guid>
    <description>&lt;p&gt;한국은행이 기준금리를 동결했다.&lt;/p&gt; 3분 전</description>
    <pubDate>Fri, 14 Aug 2026 22:30:00 GMT</pubDate>
  </item>
  <item>
    <title>제목만 있고 링크가 없다</title>
    <description>버려진다</description>
  </item>
</channel></rss>
"""

ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Latest Filings</title>
  <entry>
    <title>8-K filing</title>
    <link href="https://example.gov/b"/>
    <id>urn:example:b</id>
    <summary>Something happened.</summary>
    <updated>2026-08-14T22:30:00Z</updated>
  </entry>
</feed>
"""


def source(collection_mode: str = "feed_content", source_kind: str = "media") -> FeedSource:
    return FeedSource(
        slug="example",
        name="Example",
        source_kind=source_kind,
        country="US",
        language="en",
        feed_url="https://example.com/rss",
        collection_mode=collection_mode,
    )


def response_for(body: bytes = RSS.encode("utf-8")) -> FeedResponse:
    return FeedResponse(
        slug="example",
        url="https://example.com/rss",
        body=body,
        status=200,
        started_at=STARTED_AT,
        completed_at=COMPLETED_AT,
    )


class FakeCursor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: tuple = ()) -> None:
        self.calls.append((statement, parameters))

    def executemany(self, statement: str, parameters) -> None:
        self.calls.extend((statement, tuple(row)) for row in parameters)

    def fetchone(self) -> tuple[int]:
        return (SOURCE_RECORD_ID,)

    def fetchall(self) -> list:
        return []


class FakeConnection:
    def __init__(self) -> None:
        self.recorded_cursor = FakeCursor()

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


@pytest.fixture(autouse=True)
def without_the_psycopg2_fast_path(monkeypatch):
    """저장 테스트를 PEP 249 경로에 고정한다. `test_yahoo.py`가 먼저 쓴 방식이다."""
    monkeypatch.setattr("modules.upsert._execute_batch", None)


def inserted_columns(statement: str) -> tuple[str, ...]:
    columns = re.search(r"INSERT INTO \w+ \(([^)]+)\)", statement, re.DOTALL)
    assert columns is not None
    names = re.sub(r"--[^\n]*", "", columns.group(1))
    return tuple(name.strip() for name in names.split(",") if name.strip())


def placeholder_count(statement: str) -> int:
    values = re.search(r"VALUES \(([^)]+)\)", statement, re.DOTALL)
    assert values is not None
    return values.group(1).count("%s")


def required_columns(table: Table) -> set[str]:
    return {
        column.name
        for column in table.columns
        if not column.nullable and column.server_default is None and not column.primary_key
    }


def document_rows(cursor: FakeCursor) -> list[tuple]:
    return [parameters for statement, parameters in cursor.calls if "INSERT INTO document " in statement]


def test_document_upsert_matches_the_model_and_its_natural_key():
    table = DocumentModel.__table__
    columns = inserted_columns(DOCUMENT_UPSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)
    assert placeholder_count(DOCUMENT_UPSERT) == len(columns)

    natural_key = next(
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.name == "uq_document_natural_key"
    )
    assert f"ON CONFLICT ({', '.join(natural_key)}) DO UPDATE" in DOCUMENT_UPSERT


def test_document_upsert_does_not_touch_detected_at():
    # 처음 본 시각을 갱신하면 "언제부터 있었나"가 사라진다.
    update = DOCUMENT_UPSERT.split("DO UPDATE SET", 1)[1]
    assert "detected_at" not in update
    assert "canonical_document_id" not in update


def test_document_upsert_only_writes_when_the_hash_changed():
    # 같은 내용이면 행을 건드리지 않는다. 안 그러면 매시간 311건이 전부 updated_at만 바뀐다.
    assert "WHERE document.content_hash IS DISTINCT FROM EXCLUDED.content_hash" in DOCUMENT_UPSERT


def test_source_record_insert_matches_the_model():
    table = SourceRecord.__table__
    columns = inserted_columns(SOURCE_RECORD_INSERT)

    assert set(columns) <= {column.name for column in table.columns}
    assert required_columns(table) <= set(columns)


def test_normalize_unescapes_entities_then_strips_tags():
    # 피드는 HTML을 한 번 더 escape해서 싣는다. 태그를 먼저 지우면 그 조각이 그대로 남는다.
    assert normalize_text("&lt;p&gt;본문&lt;/p&gt;") == "본문"


def test_normalize_drops_the_space_entity_a_double_escaped_feed_leaves_behind():
    # 한국은행 보도자료는 `&amp;nbsp;`로 실어서 unescape 한 번 뒤에 `&nbsp;` 글자가 남는다.
    assert normalize_text("&lt;p&gt;기준금리를&amp;nbsp;3.00%로&lt;/p&gt;") == "기준금리를 3.00%로"


def test_normalize_survives_an_unescaped_angle_bracket():
    # BEA 요약의 escape되지 않은 `<` 하나가 XML 파서를 태워 수집 태스크를 통째로 죽였다.
    assert normalize_text("GDP < 2% growth &amp; slowing") is not None


def test_normalize_drops_the_decorations_that_move_every_hour():
    assert normalize_text("본문 3분 전 조회수 1,234") == "본문"
    assert normalize_text("Body 5 minutes ago") == "Body"
    assert normalize_text("본문 기자 name@example.com") == "본문 기자"


def test_normalize_collapses_whitespace_and_returns_none_when_empty():
    assert normalize_text("  a\n\n b  ") == "a b"
    assert normalize_text("   ") is None
    assert normalize_text(None) is None


def test_canonical_url_drops_tracking_and_fragments():
    assert canonical_url("https://e.com/a?utm_source=rss&utm_medium=feed#top") == "https://e.com/a"
    assert canonical_url("https://e.com/a?id=1&fbclid=xyz") == "https://e.com/a?id=1"


def test_content_hash_separates_the_parts():
    # 구분자가 없으면 제목 끝과 요약 앞이 붙어 서로 다른 문서가 같은 해시를 낸다.
    assert content_hash("ab", "c") != content_hash("a", "bc")


def test_content_hash_is_stable_for_the_same_input():
    assert content_hash("t", "s") == content_hash("t", "s")


def test_content_hash_keeps_the_value_it_had_when_every_body_was_null():
    """**이 값을 바꾸면 저장된 문서 전부가 재평가 대상이 된다.**

    해시는 세 조각을 이어 붙이고 세 번째가 본문 자리였다. 본문을 해시에서 뺐지만 조각 수는
    그대로 둔다 — 조각을 둘로 줄이면 구분자가 하나 빠져 이미 저장된 해시와 달라지고,
    그 순간 `assessed_content_hash`와 어긋나 본문 백필이 전량 재평가를 부른다.

    그래서 옛 값을 리터럴로 못 박는다. 공식을 다시 고칠 일이 생기면 이 테스트가 먼저 깨진다.
    """
    assert content_hash("t", "s") == "4fef3774e06bb5ee2741d6fb2d54bf2e077d188f9d76efcbc73a6b6235d6667b"
    assert content_hash("기준금리 동결", None) == "1ecf6cb81b67f9b761696f921ecfa2e701437a4650c32df7d52c75e2b418765a"


def test_content_hash_ignores_the_body():
    """평가가 본문을 보지 않으므로 본문이 바뀌었다고 다시 평가하지 않는다.

    시그니처에 본문이 아예 없다는 사실 자체가 계약이다. 인자로 받아 버리면 언젠가 누군가
    넘긴다.
    """
    assert inspect.signature(content_hash).parameters.keys() == {"title", "summary"}


def test_parse_reads_rss_items():
    items, truncated = parse_feed(RSS.encode("utf-8"))

    assert truncated is False
    # 링크가 없는 항목은 문서로 가리킬 수 없어 버린다.
    assert len(items) == 1
    item = items[0]
    assert item.external_id == "urn:example:a"
    assert item.canonical_url == "https://example.com/a"
    assert item.title == "기준금리 동결"
    assert item.summary == "한국은행이 기준금리를 동결했다."
    assert item.published_at == datetime(2026, 8, 14, 22, 30, tzinfo=UTC)


def test_parse_reads_atom_entries():
    items, _ = parse_feed(ATOM.encode("utf-8"))

    assert len(items) == 1
    assert items[0].canonical_url == "https://example.gov/b"
    assert items[0].published_at == datetime(2026, 8, 14, 22, 30, tzinfo=UTC)


EINFOMAX_RSS = """<?xml version="1.0" encoding="utf-8" ?>
<rss version="2.0"><channel>
  <title>연합인포맥스 - 전체기사</title>
  <item>
    <nsid>AKR20260819148600016</nsid>
    <title>이랜드월드 회사채 수요예측 미매각</title>
    <link>https://news.einfomax.co.kr/news/articleView.html?idxno=4430828</link>
    <description><![CDATA[이랜드월드(BBB)가 회사채 수요예측에서 모집액을 채우지 못했다.]]></description>
    <author><![CDATA[아무개 기자]]></author>
    <pubDate>2026-08-19 17:01:32</pubDate>
  </item>
</channel></rss>
"""


def test_parse_reads_the_einfomax_naive_kst_pubdate():
    # NDsoft CMS는 pubDate를 시간대 없는 KST로 준다. 출처가 선언한 시간대로 읽어
    # UTC로 정규화한다. KST 17:01:32 = UTC 08:01:32.
    items, _ = parse_feed(EINFOMAX_RSS.encode("utf-8"), "einfomax")

    assert len(items) == 1
    item = items[0]
    # guid가 없어 canonical URL이 external_id다.
    assert item.external_id == "https://news.einfomax.co.kr/news/articleView.html?idxno=4430828"
    assert item.summary == "이랜드월드(BBB)가 회사채 수요예측에서 모집액을 채우지 못했다."
    assert item.published_at == datetime(2026, 8, 19, 8, 1, 32, tzinfo=UTC)


def test_parse_still_drops_naive_times_from_undeclared_sources():
    # 시간대 선언이 없는 출처의 naive 시각은 지어내지 않고 버린다.
    items, _ = parse_feed(EINFOMAX_RSS.encode("utf-8"), "example")

    assert items[0].published_at is None


EIA_RSS = """<?xml version="1.0" encoding="ISO-8859-1" ?>
<rss version="2.0"><channel>
  <title>EIA: Press Releases</title>
  <item>
    <title>EIA expects highest natural gas inventories in a decade</title>
    <link>/pressroom/releases/press591.php</link>
    <guid isPermaLink="true">/pressroom/releases/press591.php</guid>
    <pubDate>Tue, 11 Aug 2026 12:00:00 EST</pubDate>
  </item>
</channel></rss>
"""


def test_parse_resolves_relative_links_against_the_feed_url():
    # EIA는 링크와 guid를 상대 경로로 준다. canonical_url은 절대 URL이어야 문서를
    # 가리킬 수 있다. external_id는 guid 그대로라 출처 안에서 고유하면 충분하다.
    items, _ = parse_feed(EIA_RSS.encode("utf-8"), "eia", "https://www.eia.gov/rss/press_rss.xml")

    assert len(items) == 1
    item = items[0]
    assert item.canonical_url == "https://www.eia.gov/pressroom/releases/press591.php"
    assert item.external_id == "/pressroom/releases/press591.php"
    # RFC 822 시간대 이름(EST = -0500)을 읽는다.
    assert item.published_at == datetime(2026, 8, 11, 17, 0, tzinfo=UTC)


def test_parse_leaves_absolute_links_alone_when_a_base_url_is_given():
    items, _ = parse_feed(RSS.encode("utf-8"), "example", "https://example.com/rss")

    assert items[0].canonical_url == "https://example.com/a"


CENSUS_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>U.S. Census Bureau Economic Briefing Room</title>
  <item>
    <title>New Residential Construction</title>
    <link>https://www.census.gov/construction/nrc/index.html</link>
    <description>Privately-owned housing starts in July</description>
    <pubDate>Wed, 19 Aug 2026 16:01:24 -0400</pubDate>
    <guid isPermaLink="false">housing_starts</guid>
  </item>
</channel></rss>
"""


def test_parse_appends_the_release_date_to_series_guids():
    # Census 브리핑룸은 매달 같은 guid(housing_starts)로 새 발표를 싣는다. 그대로 두면
    # (source_slug, external_id) 자연키가 같은 행을 덮어써 과거 발표가 사라진다.
    items, _ = parse_feed(CENSUS_RSS.encode("utf-8"), "census")

    assert items[0].external_id == "housing_starts:2026-08-19"


def test_parse_keeps_series_guids_untouched_for_other_sources():
    items, _ = parse_feed(CENSUS_RSS.encode("utf-8"), "example")

    assert items[0].external_id == "housing_starts"


BBC_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>BBC News</title>
  <item>
    <title>UK inflation rises</title>
    <link>https://www.bbc.co.uk/news/articles/c70gp8252ejo</link>
    <guid isPermaLink="true">https://www.bbc.co.uk/news/articles/c70gp8252ejo#1</guid>
    <pubDate>Wed, 19 Aug 2026 22:31:46 GMT</pubDate>
  </item>
</channel></rss>
"""


def test_parse_strips_the_bbc_revision_fragment_from_guids():
    # BBC guid의 #N은 개정 카운터다. 남기면 기사 수정마다 새 문서 행이 생기고 LLM 평가가
    # 다시 돈다(실측: 142행 중 고유 기사 99개).
    items, _ = parse_feed(BBC_RSS.encode("utf-8"), "bbc_business")

    assert items[0].external_id == "https://www.bbc.co.uk/news/articles/c70gp8252ejo"


def test_parse_keeps_guid_fragments_for_other_sources():
    items, _ = parse_feed(BBC_RSS.encode("utf-8"), "example")

    assert items[0].external_id == "https://www.bbc.co.uk/news/articles/c70gp8252ejo#1"


def test_parse_rejects_a_page_that_is_not_a_feed():
    # 주소가 바뀐 사이트는 404 대신 HTML 안내를 200으로 준다. 0건으로 넘기면 몇 달째 비어
    # 있어도 알 수 없다.
    with pytest.raises(DocumentPayloadError, match="not valid XML"):
        parse_feed(b"<!doctype html><html><body>Not found</body></html>")


def test_parse_accepts_a_feed_without_items():
    items, _ = parse_feed(b'<?xml version="1.0"?><rss version="2.0"><channel></channel></rss>')

    # 새 문서가 없는 시간대는 정상이다.
    assert items == ()


def test_store_writes_one_source_record_per_feed():
    connection = FakeConnection()
    items, _ = parse_feed(RSS.encode("utf-8"))

    stored, outcome = store_documents(connection, source(), response_for(), items, False, DETECTED_AT)

    records = [
        parameters
        for statement, parameters in connection.recorded_cursor.calls
        if "INSERT INTO source_record" in statement
    ]
    assert len(records) == 1
    source_type, slug, source_key, _, _, status, count, payload, metadata = records[0]
    assert (source_type, slug, source_key) == ("crawl", "example", "feed")
    assert (status, count, stored) == ("succeeded", 1, 1)
    # 피드는 XML이고 payload 컬럼은 jsonb다.
    assert payload is None
    assert json.loads(metadata)["outcome"]["item_count"] == 1
    assert outcome.slug == "example"


def test_store_writes_rows_in_the_upsert_column_order():
    connection = FakeConnection()
    items, _ = parse_feed(RSS.encode("utf-8"))

    store_documents(connection, source(), response_for(), items, False, DETECTED_AT)

    (row,) = document_rows(connection.recorded_cursor)
    (
        slug,
        external_id,
        url,
        document_type,
        title,
        summary,
        body,
        language,
        published,
        detected,
        digest,
        record,
    ) = row
    assert (slug, external_id) == ("example", "urn:example:a")
    assert url == "https://example.com/a"
    assert document_type == "article"
    assert (title, summary, body) == ("기준금리 동결", "한국은행이 기준금리를 동결했다.", None)
    assert language == "en"
    assert published == datetime(2026, 8, 14, 22, 30, tzinfo=UTC)
    assert detected == DETECTED_AT
    assert digest == content_hash(title, summary)
    assert record == SOURCE_RECORD_ID


def test_official_sources_are_stored_as_press_releases():
    connection = FakeConnection()
    items, _ = parse_feed(RSS.encode("utf-8"))

    store_documents(connection, source(source_kind="official"), response_for(), items, False, DETECTED_AT)

    assert document_rows(connection.recorded_cursor)[0][3] == "press_release"


def test_research_sources_are_stored_as_reports():
    connection = FakeConnection()
    items, _ = parse_feed(RSS.encode("utf-8"))

    store_documents(connection, source(source_kind="research"), response_for(), items, False, DETECTED_AT)

    assert document_rows(connection.recorded_cursor)[0][3] == "report"


def test_existing_external_ids_asks_by_slug_and_skips_the_query_when_nothing_to_ask():
    connection = FakeConnection()
    connection.recorded_cursor.fetchall = lambda: [("a",), ("c",)]  # type: ignore[method-assign]

    assert existing_external_ids(connection, "example", ["a", "b", "c"]) == frozenset({"a", "c"})
    statement, parameters = connection.recorded_cursor.calls[0]
    assert statement is EXISTING_EXTERNAL_IDS
    assert parameters == ("example", ["a", "b", "c"])

    assert existing_external_ids(FakeConnection(), "example", []) == frozenset()


def test_taggable_tickers_read_the_whole_instrument_master():
    """문서 평가의 종목 후보와 같은 SQL이다. `is_watched`를 거르지 않는다 — 시세를 안 받는
    종목이어도 이름을 알면 그 리포트를 받는다."""
    connection = FakeConnection()
    connection.recorded_cursor.fetchall = lambda: [("005930", "삼성전자"), ("000660", "SK하이닉스")]  # type: ignore[method-assign]

    assert taggable_tickers(connection) == frozenset({"005930", "000660"})
    assert connection.recorded_cursor.calls[0][0] is TAGGABLE_INSTRUMENTS


def test_metadata_only_sources_do_not_store_the_summary():
    connection = FakeConnection()
    items, _ = parse_feed(RSS.encode("utf-8"))

    store_documents(connection, source(collection_mode="metadata_only"), response_for(), items, False, DETECTED_AT)

    row = document_rows(connection.recorded_cursor)[0]
    assert row[5] is None
    # 해시도 요약 없이 계산해야 한다. 안 그러면 저장한 것과 해시한 것이 어긋난다.
    assert row[10] == content_hash("기준금리 동결", None)


def test_discovery_never_stores_a_body():
    """발견은 피드 한 번이고 본문은 문서마다 요청이 한 번 더 든다.

    본문 자리가 비어 있어야 그 문서가 `document_body_hourly`의 큐(`body_status IS NULL`)에
    남는다.
    """
    connection = FakeConnection()
    items, _ = parse_feed(RSS.encode("utf-8"))

    store_documents(connection, source(collection_mode="full_text"), response_for(), items, False, DETECTED_AT)

    assert document_rows(connection.recorded_cursor)[0][6] is None


def test_store_keeps_a_source_record_when_the_feed_has_no_items():
    connection = FakeConnection()

    stored, outcome = store_documents(connection, source(), response_for(), (), False, DETECTED_AT)

    assert stored == 0
    assert outcome.item_count == 0
    records = [s for s, _ in connection.recorded_cursor.calls if "INSERT INTO source_record" in s]
    # 새 문서가 없는 시간대와 아직 조회하지 않은 시간대가 구분돼야 한다.
    assert len(records) == 1
