"""네이버 증권 리서치에서 증권사 리포트를 문서로 수집한다.

뉴스가 "무슨 일이 있었다"까지라면 리포트는 그 사건이 종목·시장에 어떤 뜻인지를 쓴 것이다.
숫자(투자의견·목표주가)는 `collectors/analyst/kis_opinion.py`가 따로 받고, 추론 툴이 둘을
발표일·증권사로 이어 읽는다. 설계는 `docs/market-thesis/6-analyst.md` 3절이다.

## 출처 (실측 2026-08-21, UTF-8 JSON)

네이버 공식 Open API에는 증권·리서치가 없다. 모바일 증권이 화면을 그리려고 부르는 내부 JSON을
쓴다 — EUC-KR HTML(`finance.naver.com/research/*_list.naver`)을 파싱하는 것보다 낫다.

- 목록 `…/api/research/{category}?pageSize=N&page=1` → `researchId, title, brokerName,
  writeDate, endUrl`, 종목분석만 `itemCode, itemName`.
- 상세 `…/api/research/{category}/{researchId}` → `researchContent.content`(요약 HTML)와
  종목분석의 `opinion, goalPrice, prevGoalPrice`.

**robots.txt가 일반 봇을 막는다**(`Disallow: /`). 사용자가 감수하기로 결정했고(2026-08-21)
`document_source.terms_url`·`terms_checked_at`과 시드 리비전 주석에 남겼다. 이용조건이
문제가 되면 코드가 아니라 `enabled`를 내린다.

## KRX·금감원 목록과 다른 점

**상세를 한 번 더 받는다.** 목록은 요약을 주지 않는다. 그래서 수집이 세 단계다.

1. `fetch()` — 목록 한 번. 페이지 크기는 `feed_url`이 정한다(정책은 DB에 있다).
2. `parse()` — 목록 → `FeedItem`. 요약은 비어 있다.
3. `enrich()` — **거를 것을 먼저 거르고** 남은 새 항목만 상세를 받아 요약을 채운다.

거르기가 상세 요청 앞인 이유는 버릴 문서의 상세를 받지 않기 위해서다. 거르는 것 둘:

- **추적 밖 종목의 리포트.** 종목분석은 하루 수십 건인데 대부분 우리가 보지 않는 종목이고,
  그것까지 저장하면 LLM 평가 비용만 늘고 `recent_documents`가 관심 밖 종목으로 채워진다
  (2026-08-22 사용자 결정, 실측에서 30건 중 2건만 남았다). 종목이 없는 리포트(시황·투자전략·
  경제·채권·산업분석)는 시장 전체 이야기라 그대로 받고, 카테고리를 통째로 끄는 손잡이는
  `document_source.enabled`다.
- **이미 있는 항목.** 목록 정보로 다시 upsert하면 `content_hash`가 달라져(요약이 NULL이다)
  상세 요약이 지워지고 재평가가 돈다.

증권사 이름은 제목 **끝에** 낱말로 붙인다(`… - 대신증권`). `[대신증권]` 같은 대괄호 말머리는
`dedup`이 벗기고 비교해서, 같은 날 두 증권사가 비슷한 제목을 내면 하나가 중복으로 묶여
평가에서 빠진다.

## 클래스인 이유

**출처 한 행이 상태다.** 카테고리(`company`, `market` …)는 slug에서 뽑고 목록 URL과 상세
URL이 모두 그것으로 정해진다. 함수로 두면 세 단계가 `source`를 매번 다시 받아 카테고리를
다시 뽑는다. 상태가 필요 없는 것(목록 파싱, 요약 조립)은 `@staticmethod`다.
"""

import json
import logging
from datetime import UTC, date, datetime
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from modules.collectors.documents import (
    MAX_ITEMS_PER_FEED,
    Connection,
    DocumentPayloadError,
    FeedItem,
    FeedResponse,
    FeedSource,
    existing_external_ids,
    fetch_url,
    kst_midnight_utc,
    normalize_text,
    watched_tickers,
)

logger = logging.getLogger(__name__)

NAVER_RESEARCH_API = "https://m.stock.naver.com/api/research"
NAVER_RESEARCH_SLUG_PREFIX = "naver_research_"

# 목록 API의 카테고리. slug 접미가 이 값 그대로라 코드가 slug에서 카테고리를 뽑는다.
NAVER_RESEARCH_CATEGORIES: tuple[str, ...] = ("company", "industry", "market", "invest", "economy", "debenture")


class NaverResearchItem(BaseModel):
    """목록 배열의 항목 하나. 우리가 쓰는 칸만 받는다. `readCount`는 매시간 바뀌므로 안 받는다."""

    model_config = ConfigDict(frozen=True)

    research_id: int = Field(alias="researchId")
    title: str
    broker_name: str = Field(alias="brokerName")
    write_date: str = Field(alias="writeDate")
    end_url: str = Field(alias="endUrl")
    item_code: str | None = Field(default=None, alias="itemCode")
    item_name: str | None = Field(default=None, alias="itemName")

    @field_validator("write_date")
    @classmethod
    def require_iso_date(cls, raw: str) -> str:
        # `2026-08-21` 모양만 받는다. 모르는 표기를 조용히 엉뚱한 날짜로 만들지 않는다.
        date.fromisoformat(raw)
        if len(raw) != 10:
            raise ValueError(f"writeDate must be YYYY-MM-DD, got {raw!r}")
        return raw


class NaverResearchContent(BaseModel):
    """상세 응답의 `researchContent`. 종목분석이 아니면 의견·목표가가 없다."""

    model_config = ConfigDict(frozen=True)

    content: str | None = None
    opinion: str | None = None
    goal_price: str | None = Field(default=None, alias="goalPrice")
    prev_goal_price: str | None = Field(default=None, alias="prevGoalPrice")


class NaverResearchDetail(BaseModel):
    model_config = ConfigDict(frozen=True)

    research_content: NaverResearchContent = Field(alias="researchContent")


class NaverResearchCollector:
    """네이버 리서치 카테고리 하나의 수집기. `document_source` 한 행이 객체 하나다."""

    def __init__(self, source: FeedSource) -> None:
        self._source = source
        self._category = self.category_of(source.slug)

    @staticmethod
    def category_of(slug: str) -> str:
        category = slug.removeprefix(NAVER_RESEARCH_SLUG_PREFIX)
        if category not in NAVER_RESEARCH_CATEGORIES:
            raise DocumentPayloadError(f"{slug} is not a Naver research category")
        return category

    @property
    def category(self) -> str:
        return self._category

    @property
    def slug(self) -> str:
        return self._source.slug

    def fetch(self) -> FeedResponse:
        """목록 JSON을 한 번 받아 온다."""
        started_at = datetime.now(UTC)
        response = fetch_url(self._source.slug, self._source.feed_url)
        return FeedResponse(
            slug=self._source.slug,
            url=self._source.feed_url,
            body=response.body,
            status=response.status,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )

    @staticmethod
    def parse(body: bytes) -> tuple[tuple[FeedItem, ...], bool]:
        """목록 배열에서 항목을 뽑는다. (항목, 잘렸는지)를 돌려준다.

        배열이 아니면 실패시킨다. 경로가 바뀌면 HTML 안내가 200으로 올 수 있다. **빈 배열은
        정상이다** — 새벽과 주말에는 리포트가 없다.

        요약(`summary`)은 여기서 채우지 않는다. 상세 응답이 주고 `enrich`가 새 항목에만 받는다.
        """
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DocumentPayloadError(f"Naver research listing body is not valid JSON: {error}") from None
        if not isinstance(payload, list):
            raise DocumentPayloadError("Naver research listing JSON is not an array")

        truncated = len(payload) > MAX_ITEMS_PER_FEED
        items: list[FeedItem] = []
        for row in payload[:MAX_ITEMS_PER_FEED]:
            try:
                entry = NaverResearchItem.model_validate(row)
            except ValidationError as error:
                raise DocumentPayloadError(f"Naver research listing row is malformed: {error}") from None
            title = normalize_text(entry.title)
            if not title:
                continue
            # 증권사는 제목 끝에 낱말로. 대괄호 말머리는 dedup이 벗긴다(모듈 docstring).
            subject = f"{normalize_text(entry.item_name)}: " if entry.item_name else ""
            items.append(
                FeedItem(
                    external_id=str(entry.research_id),
                    canonical_url=urljoin(NAVER_RESEARCH_API, entry.end_url),
                    title=f"{subject}{title} - {normalize_text(entry.broker_name)}",
                    summary=None,
                    # 작성일은 네이버가 KST 기준으로 고시한 날짜다.
                    published_at=kst_midnight_utc(date.fromisoformat(entry.write_date)),
                    stock_code=entry.item_code,
                )
            )
        return tuple(items), truncated

    def enrich(self, connection: Connection, items: tuple[FeedItem, ...]) -> tuple[FeedItem, ...]:
        """거를 것을 거르고 남은 새 항목에만 상세를 받아 요약을 채운다(모듈 docstring).

        상세 요청 하나가 실패하면 그대로 올린다. DAG이 출처 단위로 격리하므로 이 출처만 이번
        시간 실패하고, 다음 시간에 같은 항목이 다시 "새 항목"이다.
        """
        watched = watched_tickers(connection)
        wanted = tuple(item for item in items if item.stock_code is None or item.stock_code in watched)
        skipped = len(items) - len(wanted)
        if skipped:
            logger.info("%s: skipped %s reports on stocks we do not track", self._source.slug, skipped)

        known = existing_external_ids(connection, self._source.slug, [item.external_id for item in wanted])
        enriched: list[FeedItem] = []
        for item in wanted:
            if item.external_id in known:
                continue
            detail = self.fetch_detail(item.external_id)
            enriched.append(item.model_copy(update={"summary": self.summarize(detail)}))
        return tuple(enriched)

    def fetch_detail(self, external_id: str) -> NaverResearchDetail:
        """상세 한 건. 테스트가 이 메서드를 바꿔 끼운다."""
        response = fetch_url(self._source.slug, f"{NAVER_RESEARCH_API}/{self._category}/{external_id}")
        try:
            return NaverResearchDetail.model_validate_json(response.body)
        except ValidationError as error:
            raise DocumentPayloadError(f"Naver research detail {external_id} is malformed: {error}") from None

    @staticmethod
    def summarize(detail: NaverResearchDetail) -> str | None:
        """요약 문단. 종목분석이면 앞에 투자의견·목표가를 붙인다.

        HTML 태그는 `normalize_text`의 첫 규칙이 벗긴다. 파서가 따로 필요 없다.
        """
        content = detail.research_content
        parts: list[str] = []
        opinion = normalize_text(content.opinion)
        goal = _won(content.goal_price, "goalPrice")
        if opinion or goal is not None:
            head = [f"투자의견 {opinion}"] if opinion else []
            if goal is not None:
                previous = _won(content.prev_goal_price, "prevGoalPrice")
                head.append(f"목표가 {goal:,}" + (f" (직전 {previous:,})" if previous is not None else ""))
            parts.append(" · ".join(head))
        text = normalize_text(content.content)
        if text:
            parts.append(text)
        return " · ".join(parts) or None

    # --- `LISTING_SOURCES` 어댑터 ------------------------------------------------
    # 레지스트리는 `(FeedSource) -> ...` 콜러블을 들고 있다. 출처마다 객체를 만들어 준다.

    @classmethod
    def fetch_listing(cls, source: FeedSource) -> FeedResponse:
        return cls(source).fetch()

    @classmethod
    def enrich_listing(
        cls, connection: Connection, source: FeedSource, items: tuple[FeedItem, ...]
    ) -> tuple[FeedItem, ...]:
        return cls(source).enrich(connection, items)


def _won(value: str | None, field: str) -> int | None:
    """목표가 칸. 비어 있으면 없음이고, 숫자가 아니면 계약이 바뀐 것이다."""
    text = (value or "").strip().replace(",", "")
    if not text or text == "0":
        return None
    if not text.isdigit():
        raise DocumentPayloadError(f"Naver research {field} is not a number: {value!r}")
    return int(text)
