"""외부 검색. **툴이 아니라 목록으로 준다.**

설계는 `docs/analysis/market-shock-capture.md` §7.2.1~§7.2.2다.

## 왜 툴이 아닌가

모델에게 검색 툴을 쥐여 주면 어떤 질의를 던지고 무엇을 받았는지를 우리가 못 쥔다. 코드가
먼저 검색해 목록으로 주면 **근거를 "준 목록의 인덱스"로만 받을 수 있어** 문서와 같은
검증이 그대로 산다. 호출도 하나로 남는다.

## 질의는 코드가 만든다

**원인을 모르는 상태에서 만들 수 있는 것만 쓴다.** "엔캐리"처럼 답을 아는 낱말을 넣으면
실전에서 만들 수 없는 질의가 된다. 포착이 아는 값은 날짜·시각·방향, 그리고 아시아가 같이
움직였는지뿐이다.

세 번째 질의가 §4의 동시성 결과와 물린다 — 2026-09-04 실측에서 그 질의의 상위 네 건이
전부 답이었다.

## 새 파이썬 의존성이 없다

Tavily는 REST/JSON이라 표준 라이브러리 `urlopen`으로 부른다. `collectors/kis.py`와 같은
형태다. `scrapling`은 HTML을 긁을 때 쓰는 것이라 여기엔 과하다.
"""

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from pydantic import AwareDatetime, BaseModel, ConfigDict, SecretStr

from modules.shock.domain import Direction, PeerMove, PeerRegion
from modules.shock.render import moved_together
from modules.utility import KST_TIMEZONE

logger = logging.getLogger(__name__)

PROVIDER = "tavily"
ENDPOINT = "https://api.tavily.com/search"
REQUEST_TIMEOUT_SECONDS = 20

# 질의 하나가 돌려받는 결과 수. 무료 한도가 월 1,000이고 basic search가 1크레딧이라
# 이벤트당 질의 셋 × 시도 셋이어도 월 80회 남짓이다.
MAX_RESULTS = 10

# 아시아 시장 이만큼이 같은 방향이면 "아시아가 같이 움직였다"로 보고 세 번째 질의를
# 만든다. 임계가 아니라 방향만 본다 — 크기 판정은 사람이 표를 보고 한다.
MIN_MOVING_PEERS = 2


class SearchError(RuntimeError):
    """검색 제공처가 거절했고 다시 불러도 같은 답이다."""


class SearchHit(BaseModel):
    """검색 결과 하나. `market_shock_search_hit` 한 행이 될 모양이다."""

    model_config = ConfigDict(frozen=True)

    query: str
    rank: int
    title: str
    url: str
    publisher: str
    snippet: str
    published_at: AwareDatetime | None = None
    relevance: Decimal | None = None


def build_queries(
    *,
    detected_at: datetime,
    direction: Direction,
    peers: list[PeerMove],
) -> list[str]:
    """포착 값에서 질의를 조립한다. **모델이 정하지 않는다.**

    세 번째는 아시아가 같이 움직였을 때만 만든다 — 안 움직였는데 "동시 급락"을 물으면
    없는 사실을 질의가 주장하게 된다.
    """
    kst = detected_at.astimezone(KST_TIMEZONE)
    word = "급락" if direction is Direction.DROP else "급등"
    ampm = "오전" if kst.hour < 12 else "오후"
    hour = kst.hour if kst.hour <= 12 else kst.hour - 12

    queries = [
        f"코스피 {kst.month}월 {kst.day}일 {word} 이유",
        f"코스피 {kst.year}년 {kst.month}월 {kst.day}일 {ampm} {hour}시 {word} 원인",
    ]
    # **아시아만 센다.** 미국 선물이 같이 움직인 것은 "한중일 동시"가 아니라 글로벌이라,
    # 그 질의를 던지면 없는 사실을 질의가 주장한다.
    if moved_together(direction, peers, PeerRegion.ASIA) >= MIN_MOVING_PEERS:
        queries.append(f"한국 일본 중국 증시 동시 {word} {kst.month}월 {kst.day}일")
    return queries


def _published_at(raw: str | None) -> datetime | None:
    """Tavily가 주는 RFC 2822 형식(`Thu, 03 Sep 2026 08:30:00 GMT`)을 UTC로."""
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        logger.warning("could not parse published_date %r", raw)
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _host(url: str) -> str:
    return urlparse(url).netloc or url[:64]


class TavilySearch:
    """API 키를 쥔다. 질의는 호출마다 바뀌므로 메서드 인자다."""

    def __init__(self, api_key: SecretStr, *, timeout: int = REQUEST_TIMEOUT_SECONDS) -> None:
        self._api_key = api_key
        self._timeout = timeout

    def search(self, query: str, *, max_results: int = MAX_RESULTS) -> list[SearchHit]:
        """한 질의를 던지고 결과를 받는다. 0건일 수 있다.

        HTTP 4xx는 설정·키 문제라 `SearchError`로 올린다 — 다시 불러도 같다. 네트워크
        오류(`URLError`)는 그대로 올려 Airflow가 재시도하게 둔다.
        """
        body = json.dumps(
            {
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "topic": "news",
            }
        ).encode()
        request = Request(
            ENDPOINT,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            },
        )
        try:
            with urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read())
        except HTTPError as error:
            # 본문에 키가 실릴 일은 없지만 상태와 사유만 올린다.
            raise SearchError(f"{PROVIDER} rejected the search: HTTP {error.code} {error.reason}") from error
        except json.JSONDecodeError as error:
            raise SearchError(f"{PROVIDER} returned a body that is not JSON") from error
        except URLError:
            raise

        results = payload.get("results")
        if not isinstance(results, list):
            raise SearchError(f"{PROVIDER} response has no results list")

        hits = []
        for rank, item in enumerate(results, start=1):
            url = item.get("url")
            if not url:
                continue
            relevance = item.get("score")
            hits.append(
                SearchHit(
                    query=query,
                    rank=rank,
                    title=(item.get("title") or "")[:500],
                    url=url,
                    publisher=_host(url),
                    snippet=item.get("content") or "",
                    published_at=_published_at(item.get("published_date")),
                    relevance=Decimal(str(relevance)) if isinstance(relevance, int | float) else None,
                )
            )
        return hits


def collect(
    client: TavilySearch,
    queries: list[str],
    *,
    published_after: datetime,
) -> list[SearchHit]:
    """질의 여러 개를 돌리고 합친다. **같은 URL은 처음 본 것만 남긴다.**

    `published_after`보다 앞선 기사는 버린다 — 재료는 대개 며칠 전부터 있고, 그것을 근거로
    받으면 "전부터 있던 것"이 그날 그 시각의 방아쇠로 둔갑한다. 문서 창과 같은 규칙이다.

    **발행 시각을 안 주는 결과는 남긴다.** 버리면 제공처가 날짜를 못 준 것과 오래된 것이
    같아진다. 그 판단은 모델이 발췌를 보고 한다.
    """
    seen: dict[str, SearchHit] = {}
    dropped = 0
    for query in queries:
        for hit in client.search(query):
            if hit.url in seen:
                continue
            if hit.published_at is not None and hit.published_at < published_after:
                dropped += 1
                continue
            seen[hit.url] = hit
    logger.info(
        "search returned %s hit(s) over %s quer(ies); %s dropped as older than %s",
        len(seen),
        len(queries),
        dropped,
        published_after.isoformat(),
    )
    return list(seen.values())
