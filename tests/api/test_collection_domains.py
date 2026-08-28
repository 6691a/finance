"""문서·수급·사건·수집 원장 라우트.

**리소스가 넷이라 파일 하나에 둔다.** 각각이 얇고(조회 하나에 매핑 하나) 검사하는 것도
같은 성격이라, 넷을 파일 넷으로 가르면 같은 가짜 리포지토리 뼈대가 네 번 복사된다.

주제 넷: ① 정적 경로가 동적 id보다 먼저 먹는다 ② 목록에 본문·payload가 안 실린다
③ 단위와 축이 섞이지 않는다 ④ null이 0으로 바뀌지 않는다.
"""

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from dependency_injector import providers

from apps.api.app import create_app
from apps.api.repository import DocumentDetailRows, DocumentListRows, SourceRows
from apps.api.repository.collection import CollectionReadRepository
from apps.api.repository.document import DocumentReadRepository
from apps.models.content import CollectionMode, Direction, Document, DocumentType
from tests.api.conftest import container

AT = datetime(2026, 8, 27, 4, 30, tzinfo=UTC)


def document_row(document_id: int = 1, score: int | None = 7) -> Document:
    """**진짜 ORM 행이다.** 행 묶음이 `Document` 인스턴스를 요구하고, 그 검사가
    "매퍼가 실제 칸 이름을 읽는가"를 함께 지킨다."""
    return Document(
        id=document_id,
        source_slug="cnbc",
        external_id=f"ext-{document_id}",
        title="반도체 수출 <script>alert(1)</script>",
        document_type=DocumentType.ARTICLE,
        published_at=AT,
        language="en",
        content_level=CollectionMode.FULL_TEXT,
        canonical_url="https://example.test/a",
        value_score=score,
        direction=Direction.POSITIVE if score is not None else None,
        assessed_at=AT if score is not None else None,
        llm_model="grok-4.6" if score is not None else None,
        prompt_version="3" if score is not None else None,
        body="본문 전문",
        summary="요약",
        assessment="평가 근거",
        detected_at=AT,
        content_hash="abc",
        assessed_content_hash="abc",
    )


class FakeDocuments:
    def __init__(self, **rows: Any) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def list_rows(self, **kwargs: Any) -> DocumentListRows:
        self.calls.append(kwargs)
        return DocumentListRows(
            documents=tuple(self.rows.get("documents", [])),
            has_more=self.rows.get("has_more", False),
            instruments=self.rows.get("instruments", {}),
            indicators=self.rows.get("indicators", {}),
        )

    async def detail_rows(self, document_id: int) -> DocumentDetailRows | None:
        found = next((row for row in self.rows.get("documents", []) if row.id == document_id), None)
        return None if found is None else DocumentDetailRows(document=found)

    async def source_rows(self, **kwargs: Any) -> SourceRows:
        self.calls.append(kwargs)
        return SourceRows(
            sources=tuple(self.rows.get("sources", [])), coverage=self.rows.get("coverage", {})
        )

    async def disclosure_rows(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        self.calls.append(kwargs)
        return tuple(self.rows.get("disclosures", [])), False

    async def earnings_rows(self, stock_codes: Any = (), **kwargs: Any) -> tuple[Any, bool]:
        self.calls.append({"stock_codes": list(stock_codes), **kwargs})
        return tuple(self.rows.get("earnings", [])), False


class FakePositioning:
    def __init__(self, **rows: Any) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    def _grab(self, name: str, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        self.calls.append({"name": name, **kwargs})
        return tuple(self.rows.get(name, [])), False

    async def investor_flows(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        return self._grab("investor_flows", **kwargs)

    async def market_movement(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        return self._grab("market_movement", **kwargs)

    async def stock_flows(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        return self._grab("stock_flows", **kwargs)

    async def estimates(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        return self._grab("estimates", **kwargs)

    async def short_sale(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        return self._grab("short_sale", **kwargs)

    async def lending(self, **kwargs: Any) -> tuple[tuple[Any, ...], tuple[Any, ...], bool]:
        self.calls.append({"name": "lending", **kwargs})
        return (
            tuple(self.rows.get("market_lending", [])),
            tuple(self.rows.get("stock_lending", [])),
            False,
        )

    async def credit_balance(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        return self._grab("credit_balance", **kwargs)

    async def market_funds(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        return self._grab("market_funds", **kwargs)

    async def credit_ranking(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool, tuple[date, ...]]:
        self.calls.append({"name": "credit_ranking", **kwargs})
        return tuple(self.rows.get("ranking", [])), False, tuple(self.rows.get("dates", []))


class FakeEvents:
    def __init__(self, **rows: Any) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def claims(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        self.calls.append(kwargs)
        return tuple(self.rows.get("claims", [])), False

    async def outcomes(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        return tuple(self.rows.get("outcomes", [])), False

    async def extractions(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        return tuple(self.rows.get("extractions", [])), False

    async def signals(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        self.calls.append(kwargs)
        return tuple(self.rows.get("signals", [])), False

    async def opinions(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        return tuple(self.rows.get("opinions", [])), False


class FakeCollection:
    def __init__(self, **rows: Any) -> None:
        self.rows = rows
        self.calls: list[dict[str, Any]] = []

    async def health_rows(self, since: datetime, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        self.calls.append({"since": since, **kwargs})
        return tuple(self.rows.get("health", [])), False

    async def record_rows(self, **kwargs: Any) -> tuple[tuple[tuple[Any, ...], ...], bool]:
        self.calls.append(kwargs)
        return tuple(self.rows.get("records", [])), False

    async def instrument_rows(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        self.calls.append(kwargs)
        return tuple(self.rows.get("instruments", [])), False

    async def session_rows(self, **kwargs: Any) -> tuple[tuple[Any, ...], bool]:
        self.calls.append(kwargs)
        return tuple(self.rows.get("sessions", [])), False


def client(**fakes: Any) -> httpx.AsyncClient:
    built = container()
    for name, fake in fakes.items():
        getattr(built, f"{name}_repository").override(providers.Object(fake))
    app = create_app(built)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


# --- 문서 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_static_document_routes_win_over_the_document_id():
    """`sources`가 정수로 파싱되면 422가 된다. 실제 요청을 보내야 확인된다."""
    async with client(document=FakeDocuments()) as http:
        assert (await http.get("/api/documents/sources")).status_code == 200
        assert (await http.get("/api/documents/disclosures")).status_code == 200
        assert (await http.get("/api/documents/earnings")).status_code == 200


@pytest.mark.asyncio
async def test_the_document_list_never_carries_the_body():
    """3천 건의 본문을 실으면 응답이 수십 MB가 된다."""
    rows = {"documents": [document_row()], "instruments": {1: ("005930",)}}
    async with client(document=FakeDocuments(**rows)) as http:
        payload = (await http.get("/api/documents")).json()

    item = payload["items"][0]
    assert "body" not in item
    assert "assessment" not in item
    assert item["instruments"] == ["005930"]


@pytest.mark.asyncio
async def test_the_document_detail_carries_the_body_and_the_assessment():
    """원문과 평가를 한 화면에서 맞춰 봐야 "왜 근거로 뽑혔나"가 읽힌다."""
    async with client(document=FakeDocuments(documents=[document_row()])) as http:
        payload = (await http.get("/api/documents/1")).json()

    assert payload["body"] == "본문 전문"
    assert payload["assessment"] == "평가 근거"
    assert payload["value_score"] == 7


@pytest.mark.asyncio
async def test_an_unassessed_document_keeps_null_and_never_becomes_zero():
    """`value_score`가 null인 것은 "낮다"가 아니라 "아직 안 봤다"이다."""
    async with client(document=FakeDocuments(documents=[document_row(score=None)])) as http:
        item = (await http.get("/api/documents")).json()["items"][0]

    assert item["value_score"] is None
    assert item["assessed_at"] is None


@pytest.mark.asyncio
async def test_a_missing_document_is_a_404():
    async with client(document=FakeDocuments()) as http:
        assert (await http.get("/api/documents/999")).status_code == 404


def test_the_document_query_filters_by_tag_with_a_subquery():
    """태그는 다른 테이블이라 조인이 아니라 `IN`으로 건다 — 조인하면 문서가 태그 수만큼 복제된다."""
    compiled = str(
        DocumentReadRepository.list_statement(
            published_from=AT, published_to=AT, instrument="005930", indicator="DGS10"
        ).compile(compile_kwargs={"literal_binds": True})
    )

    assert "document_instrument" in compiled
    assert "document_indicator" in compiled
    assert compiled.count("FROM document \n") <= 1


# --- 수급 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_intraday_and_the_settled_flows_are_separate_routes():
    """앞은 분 단위 추정이고 뒤는 마감 뒤 확정이다. 한 표에 놓으면 못 가른다."""
    async with client(positioning=FakePositioning()) as http:
        assert (await http.get("/api/positioning/investor-flows")).status_code == 200
        assert (await http.get("/api/positioning/stock-flows")).status_code == 200


@pytest.mark.asyncio
async def test_the_settled_flow_carries_every_institution_bucket():
    """**제공처가 기관을 일곱으로 나눠 준다.** 합쳐서 내면 "연기금이 샀나 금융투자가 샀나"를
    화면에서 되물을 수 없다. 기타법인·기타단체는 기관계 **밖**이라 함께 내되 합치지 않는다.
    """
    buckets = {
        "securities_net_buy_qty": 1,
        "investment_trust_net_buy_qty": 2,
        "private_equity_net_buy_qty": 3,
        "bank_net_buy_qty": 4,
        "insurance_net_buy_qty": 5,
        "merchant_bank_net_buy_qty": 6,
        "pension_fund_net_buy_qty": 7,
    }
    row = SimpleNamespace(
        stock_code="005930",
        business_date=date(2026, 8, 26),
        close_price=Decimal(70000),
        accumulated_volume=100,
        accumulated_trade_amount=Decimal(200),
        foreign_net_buy_qty=10,
        foreign_registered_net_buy_qty=8,
        foreign_unregistered_net_buy_qty=2,
        # 세부 일곱의 합과 같아야 한다는 것은 수집기가 검증한다. 여기서는 그 값을 그대로 낸다.
        institution_net_buy_qty=sum(buckets.values()),
        individual_net_buy_qty=-38,
        other_corporation_net_buy_qty=9,
        other_organization_net_buy_qty=11,
        foreign_net_buy_amount=Decimal(1),
        institution_net_buy_amount=Decimal(2),
        individual_net_buy_amount=Decimal(3),
        **buckets,
    )
    async with client(positioning=FakePositioning(stock_flows=[row])) as http:
        payload = (await http.get("/api/positioning/stock-flows")).json()

    item = payload["items"][0]
    for name, value in buckets.items():
        assert item[name] == value
    assert item["institution_net_buy_qty"] == sum(buckets.values())
    # 기관계 밖이라 세부와 더하지 않는다.
    assert item["other_corporation_net_buy_qty"] == 9
    assert item["other_organization_net_buy_qty"] == 11
    # 외국인은 등록+미등록으로 갈린다.
    assert item["foreign_registered_net_buy_qty"] + item["foreign_unregistered_net_buy_qty"] == 10


@pytest.mark.asyncio
async def test_lending_splits_market_and_stock_into_two_arrays():
    """축은 같지만 단위와 뜻이 다르다. 한 배열에 섞으면 화면이 못 가른다."""
    rows = {
        "market_lending": [
            SimpleNamespace(
                market_code="KOSPI",
                business_date=date(2026, 8, 26),
                index_close=Decimal(3200),
                new_quantity=10,
                repayment_quantity=5,
                balance_quantity=100,
                balance_amount=Decimal(1000),
            )
        ],
        "stock_lending": [
            SimpleNamespace(
                stock_code="005930",
                business_date=date(2026, 8, 26),
                close_price=Decimal(70000),
                new_quantity=1,
                repayment_quantity=2,
                balance_quantity=3,
                balance_amount=Decimal(4),
                balance_change_quantity=-1,
            )
        ],
    }
    async with client(positioning=FakePositioning(**rows)) as http:
        payload = (await http.get("/api/positioning/lending")).json()

    assert {"market", "stock", "limit", "offset", "has_more"} <= set(payload)
    assert payload["market"][0]["market_code"] == "KOSPI"
    assert payload["stock"][0]["stock_code"] == "005930"


@pytest.mark.asyncio
async def test_the_credit_ranking_picks_one_day_and_offers_the_rest():
    """날짜마다 순위가 다시 매겨져 구간으로 주면 같은 종목이 여러 번 나온다."""
    rows = {
        "ranking": [
            SimpleNamespace(
                standard_date=date(2026, 8, 26),
                comparison_date=date(2026, 8, 25),
                rank=1,
                stock_code="005930",
                stock_name="삼성전자",
                close_price=Decimal(70000),
                loan_balance_quantity=10,
                loan_balance_amount=Decimal(700000),
                loan_balance_rate=Decimal("0.5"),
                loan_balance_growth_rate=Decimal("1.2"),
            )
        ],
        "dates": [date(2026, 8, 26), date(2026, 8, 25)],
    }
    async with client(positioning=FakePositioning(**rows)) as http:
        payload = (await http.get("/api/positioning/credit-ranking")).json()

    assert payload["items"][0]["rank"] == 1
    assert payload["standard_dates"] == ["2026-08-26", "2026-08-25"]


@pytest.mark.asyncio
async def test_the_intraday_window_is_converted_from_kst_days_to_utc():
    """분 단위 데이터라 날짜 경계가 곧 그 세션의 경계다."""
    fake = FakePositioning()
    async with client(positioning=fake) as http:
        await http.get("/api/positioning/investor-flows", params={"from": "2026-08-26", "to": "2026-08-26"})

    call = fake.calls[0]
    assert call["start"].isoformat() == "2026-08-25T15:00:00+00:00"
    assert call["end"].isoformat() == "2026-08-26T15:00:00+00:00"


# --- 사건·신호 --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_verdict_that_was_never_made_stays_null():
    """실제값 주장이 갈리면 판정하지 않는다. 그 보류가 null로 남는다."""
    rows = {
        "outcomes": [
            SimpleNamespace(
                stock_code="005930",
                event_type="earnings",
                period_key="2026Q2",
                metric="revenue",
                expected_value=Decimal(100),
                expectation_count=3,
                actual_value=None,
                surprise_pct=None,
                verdict=None,
                announced_at=AT,
                actual_ref=None,
            )
        ]
    }
    async with client(event=FakeEvents(**rows)) as http:
        item = (await http.get("/api/events/outcomes")).json()["items"][0]

    assert item["verdict"] is None
    assert item["actual_value"] is None
    assert item["expectation_count"] == 3


@pytest.mark.asyncio
async def test_the_extraction_ledger_keeps_zero_claim_rows():
    """"뽑았는데 없었다"와 "아직 안 뽑았다"가 구분돼야 매시간 같은 문서를 다시 안 뽑는다."""
    rows = {
        "extractions": [
            SimpleNamespace(
                document_id=5,
                extracted_at=AT,
                claim_count=0,
                llm_model="grok-4.6",
                prompt_version="1",
                extracted_content_hash="abc",
            )
        ]
    }
    async with client(event=FakeEvents(**rows)) as http:
        item = (await http.get("/api/events/extractions")).json()["items"][0]

    assert item["claim_count"] == 0


@pytest.mark.asyncio
async def test_the_signal_axis_is_a_trading_day_not_a_timestamp():
    fake = FakeEvents()
    async with client(event=fake) as http:
        await http.get("/api/events/signals", params={"from": "2026-08-01", "to": "2026-08-27"})

    call = next(entry for entry in fake.calls if "start" in entry)
    assert call["start"] == date(2026, 8, 1)
    assert call["end"] == date(2026, 8, 27)


# --- 수집 원장 --------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_health_summary_puts_the_stalest_source_first():
    """이 화면의 질문이 "무엇이 안 들어오고 있나"다.

    **정렬을 조회문이 한다.** 쪽을 나눈 뒤 Python에서 정렬하면 그 쪽 안에서만 늦은 것이
    위로 오므로, 순서의 보장은 SQL에 있어야 한다.
    """
    compiled = str(CollectionReadRepository.health_statement(AT))
    assert "ORDER BY max(source_record.started_at) ASC" in compiled

    rows = {
        "health": [
            ("mof", "api", 2, 2, 0, 0, 0, 20, datetime(2026, 8, 20, 5, tzinfo=UTC)),
            ("kis", "api", 10, 9, 1, 0, 0, 100, datetime(2026, 8, 27, 5, tzinfo=UTC)),
        ]
    }
    async with client(collection=FakeCollection(**rows)) as http:
        payload = (await http.get("/api/collection/health")).json()

    assert [item["source"] for item in payload["items"]] == ["mof", "kis"]
    assert payload["items"][1]["failed"] == 1


@pytest.mark.asyncio
async def test_the_record_list_says_whether_a_payload_exists_without_sending_it():
    """jsonb 원본이라 행 하나가 수백 KB일 수 있다."""
    rows = {
        "records": [
            (1, "api", "kis", "005930", AT, AT, "succeeded", 390, True, None),
        ]
    }
    async with client(collection=FakeCollection(**rows)) as http:
        item = (await http.get("/api/collection/records")).json()["items"][0]

    assert item["has_payload"] is True
    assert "payload" not in item


def test_the_record_query_never_selects_the_payload_column():
    compiled = str(
        CollectionReadRepository.record_statement(start=AT, end=AT).compile(
            compile_kwargs={"literal_binds": True}
        )
    )

    assert "source_record.payload IS NOT NULL" in compiled
    assert "source_record.payload AS" not in compiled


def test_the_health_query_counts_every_status_separately():
    """`running`이 성공·실패에 묻히면 "끊긴 것이 있나"의 신호가 사라진다."""
    compiled = str(
        CollectionReadRepository.health_statement(AT).compile(compile_kwargs={"literal_binds": True})
    )

    for status in ("succeeded", "failed", "running", "quarantined"):
        assert f"AS {status}" in compiled
