"""급변 포착의 상수와 데이터 모양. **Airflow도 DB도 import하지 않는다.**

설계는 `docs/analysis/market-shock-capture.md`다.

여기 있는 숫자 넷(임계·창·지연·쿨다운)이 이 기능의 손잡이 전부다. DAG의 `Param`이
기본값으로 이 값을 쓰고, 저장된 행에도 임계가 함께 남아 손잡이를 옮긴 뒤 옛 행과 섞이지
않는다.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# 트리거 대상. `index_bar.symbol`과 같은 값이다.
TRIGGER_SYMBOL = "KOSPI"
BAR_PROVIDER = "kis"

# 창 안의 극값 대비 ±이 값이면 급변이다. **양방향이다.**
#
# 14거래일 실측(2026-09-04)에서 ±2.0%가 6건(하락 3, 상승 3)으로 한 달 8.6회, 주 2회다.
# ±1.5%는 한 달 14회로 이틀에 한 번이라 안 읽히고, ±2.5%는 한 달 3회로 안 울린다.
# 표본이 14일뿐이라 이 값은 통계가 아니라 관찰이다 — 이벤트 20건 또는 3개월에 다시 잰다.
THRESHOLD_PCT = Decimal("2.0")

# 판정하는 창의 길이(분).
#
# 15분은 2026-09-03 사건을 ±2%로 못 잡고(그 창의 최악이 -2.35%), 45분은 같은 임계에서
# 오탐이 는다(한 달 8.6 → 실측 기준 상승 쪽이 개장 갭으로 늘어난다).
WINDOW_MINUTES = 30

# 창의 끝을 실행 시각보다 이만큼 앞에 둔다(분).
#
# **니케이가 KIS에서도 15~16분 지연이고 아시아 수집이 5분 폴링이다.** 창을 "지금"까지
# 잡으면 아시아 칸이 비어서 포착의 핵심("한국만의 문제가 아닐 수 있다")이 빠진다.
# 지연 16 + 폴링 5 + 여유 4다.
LAG_MINUTES = 25

# 이 시간 안에 이미 포착한 사건이 있으면 새로 만들지 않는다(분).
#
# 한 급락이 30분 이어지면 5분 폴링에 여섯 번 걸린다. 자연키 `(symbol, detected_at)`으로는
# 못 막는다 — 낙폭이 깊어지면서 **다른 봉**이 임계에 닿기 때문이다.
COOLDOWN_MINUTES = 60

# 창이 이만큼 안 차면 판정하지 않는다. 창 길이의 절반이다.
MIN_TRIGGER_BARS = WINDOW_MINUTES // 2

# 창을 이만큼 못 채운 시장은 값이 아니라 "데이터 없음"이다.
MIN_PEER_BARS = WINDOW_MINUTES // 2

INDEX_BAR_TABLE = "index_bar"
INDEX_FUTURE_BAR_TABLE = "index_future_bar"


class PeerRegion(StrEnum):
    """비교 시장을 묶는 축. 메시지의 소제목이자 검색 질의의 판단 근거다."""

    ASIA = "asia"
    US = "us"


class PeerSpec(BaseModel):
    """나란히 볼 시장 하나가 어느 표의 어느 심볼인가."""

    model_config = ConfigDict(frozen=True)

    symbol: str
    label: str
    region: PeerRegion
    provider: str
    table: str
    note: str = ""


# 같은 창에서 나란히 볼 시장. **코스피 자신도 코스닥도 넣지 않는다.**
#
# 묻는 것이 "한국만의 재료인가"라서 코스닥은 답이 못 된다 — 같은 나라, 같은 시각, 같은
# 수급이라 거의 언제나 같이 움직인다. 가르는 힘이 있는 것은 **밖**이다.
#
# **미국은 현물이 아니라 선물이다.** 한국 장중(09:00~15:30 KST)에 미국 현물장은 닫혀
# 있고, 그 시간에 움직이는 것이 지수선물이다. `yahoo_quote_intraday`가 그것을 노리고
# 시간 창 없이 도는 유일한 수집이라 30분 창이 늘 찬다(2026-09-04 실측: 창 하나에 36봉).
#
# 아시아 넷은 "한국만인가 아시아 전체인가"를, 미국 선물 둘은 "아시아만인가 글로벌인가"를
# 가른다. 셋째 층이 있어야 2026-09-03처럼 아시아만 흔들린 날을 알아본다.
PEERS: tuple[PeerSpec, ...] = (
    PeerSpec(
        symbol="NIKKEI225",
        label="닛케이225",
        region=PeerRegion.ASIA,
        provider=BAR_PROVIDER,
        table=INDEX_BAR_TABLE,
        # 지연이 큰 시장은 라벨에 그 사실을 적는다. 값만 보면 "지금"으로 읽힌다.
        note="15분 지연",
    ),
    PeerSpec(
        symbol="SSE_COMP",
        label="상해종합",
        region=PeerRegion.ASIA,
        provider=BAR_PROVIDER,
        table=INDEX_BAR_TABLE,
    ),
    PeerSpec(
        symbol="HSI",
        label="항셍",
        region=PeerRegion.ASIA,
        provider=BAR_PROVIDER,
        table=INDEX_BAR_TABLE,
    ),
    PeerSpec(
        symbol="TAIEX",
        label="대만가권",
        region=PeerRegion.ASIA,
        provider=BAR_PROVIDER,
        table=INDEX_BAR_TABLE,
    ),
    PeerSpec(
        symbol="SP500_FUT",
        label="S&P500 선물",
        region=PeerRegion.US,
        provider="yahoo",
        table=INDEX_FUTURE_BAR_TABLE,
    ),
    PeerSpec(
        symbol="NASDAQ100_FUT",
        label="나스닥100 선물",
        region=PeerRegion.US,
        provider="yahoo",
        table=INDEX_FUTURE_BAR_TABLE,
    ),
)

PEER_SPECS: dict[str, PeerSpec] = {spec.symbol: spec for spec in PEERS}
REGION_LABELS: dict[PeerRegion, str] = {PeerRegion.ASIA: "아시아", PeerRegion.US: "미국 선물"}

# 퍼센트 칸의 자릿수. `market_shock_event`의 `Numeric(10, 4)`와 같다.
PCT_EXPONENT = Decimal("0.0001")


class Direction(StrEnum):
    """급변의 방향. `apps/models/market/shock.py`의 `ShockDirection`과 값이 같다.

    두 트리가 서로를 import하지 않아 같은 값 집합이 양쪽에 한 벌씩 있다(저장소 규칙).
    어긋나면 `tests/models/test_market_models.py`가 잡는다.
    """

    DROP = "drop"
    SURGE = "surge"


class _State(BaseModel):
    """이 모듈의 데이터 모양이 공유하는 설정.

    **재시도 경로에서 값이 바뀌면 원본과 저장값이 어긋난다**(저장소 규칙).
    """

    model_config = ConfigDict(frozen=True)


class Bar(_State):
    """1분봉 하나. `index_bar`의 한 행에서 판정에 쓰는 칸만 뽑았다."""

    bar_at: AwareDatetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class PeerMove(_State):
    """같은 창의 다른 시장 하나.

    **`available=False`면 `change_pct`가 `None`이다.** 0으로 채우지 않는다 — 빈 칸은
    "안 움직였다"가 아니라 "못 봤다"이고, 둘을 섞으면 포착이 거짓말을 한다.
    """

    symbol: str
    label: str
    region: PeerRegion = PeerRegion.ASIA
    change_pct: Decimal | None = None
    bars: int = 0
    available: bool = False


class ShockEvent(_State):
    """포착한 급변 하나. 저장 전의 모양이라 `peers`는 아직 없다."""

    symbol: str
    direction: Direction
    detected_at: AwareDatetime
    window_start: AwareDatetime
    window_end: AwareDatetime
    extreme_at: AwareDatetime
    extreme_price: Decimal
    trigger_price: Decimal
    move_pct: Decimal
    window_change_pct: Decimal | None
    bar_count: int
    threshold_pct: Decimal


# --- 원인 분석 (사후) ---------------------------------------------------------

# 프롬프트 판. 문장을 고치면 이 값을 올리고 `tests/modules/test_prompt_versions.py`의
# 해시도 같은 커밋에서 바꾼다. 안 올리면 서로 다른 프롬프트의 판정이 한 판으로 섞인다.
CAUSE_PROMPT_VERSION = "1"

# 프롬프트에 싣는 문서 수. 3영업일 창이면 후보가 수백 건이라 상한이 필요하다.
MAX_DOCUMENTS = 40

# 프롬프트에 실을 문서의 가치 점수 하한.
#
# **`ORDER BY value_score DESC LIMIT N`만으로는 안 걸러진다.** 후보가 상한보다 적으면
# `LIMIT`이 아무것도 안 자르고 창 안 전부가 그대로 모델에 간다. 그러면 부고·인사·행사
# 기사가 근거 후보로 들어온다 — `recent_news`가 정확히 그 결함을 맞았고(main의
# `NEWS_MIN_VALUE_SCORE`, 2026-09-04) 이 쿼리도 같은 모양이라 같은 하한을 둔다.
#
# 원인 분석의 창은 사건 시각 이후라 1차 시도에서 특히 좁다. 그때가 제일 취약하다.
#
# **`kospi.domain.NEWS_MIN_VALUE_SCORE`와 값이 같지만 import하지 않는다** — 두 도메인이
# 서로를 모르는 것이 우선이고, 어긋나면 테스트가 잡는다.
MIN_DOCUMENT_SCORE = 4

# 검색 결과 수(질의 여러 개를 합친 뒤의 상한).
MAX_SEARCH_RESULTS = 10

# 원인 문장의 길이 상한. 한 문장이라는 규칙을 코드가 강제하는 자리다.
MAX_CAUSE_CHARS = 300

# 원인을 찾는 기한. 포착일부터 이만큼 뒤의 KRX 개장일이다. 날짜는 우리가 세지 않는다 —
# `market_session.effective_open_day`가 판정의 주인이다.
CAUSE_BUSINESS_DAYS = 3

# 한 실행이 처리할 이벤트 수. 급변이 한 달 8.6건이라 평소 0~2건이고, 이 값은 폭주만 막는다.
MAX_EVENTS_PER_RUN = 20


class CauseStatus(StrEnum):
    """원인 분석의 상태. `apps/models/market/shock.py`의 `ShockCauseStatus`와 값이 같다."""

    PENDING = "pending"
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


class CauseKind(StrEnum):
    """무엇이 방아쇠였나. **수급은 경로이지 방아쇠가 아니다.**

    앵커 없이 물었을 때 두 모델이 갈렸다 — 기관 순매도를 gpt는 `unclear`로, grok은
    `confirmed`로 답했다(2026-09-04). 프롬프트에 앵커를 넣자 둘 다 `unclear`가 됐다.
    """

    RUMOR = "rumor"
    CONFIRMED = "confirmed"
    UNCLEAR = "unclear"


class DocumentRow(_State):
    """프롬프트에 실리는 문서 하나. `shock_documents/select_after_event.sql`의 한 행이다."""

    id: int
    published_at: AwareDatetime
    source_slug: str
    title: str
    value_score: int | None = None
    reason: str = ""
    new_facts: tuple[str, ...] = ()


class SearchRow(_State):
    """프롬프트에 실리는 검색 결과 하나. **번호는 그 시도 안에서만 뜻이 있다.**

    모델은 이 번호로 답하고 코드가 그것을 URL로 되돌려 `market_shock_search_hit.cited`를
    찍는다 — URL이 행의 자연키라 다음에도 같은 것을 가리킨다.
    """

    index: int
    title: str
    url: str
    publisher: str
    snippet: str
    published_at: AwareDatetime | None = None


class CauseInput(_State):
    """원인 분석 한 번의 입력 전부. 프롬프트 조립이 이 모델만 본다."""

    shock_event_id: int
    symbol: str
    direction: Direction
    detected_at: AwareDatetime
    extreme_at: AwareDatetime
    extreme_price: Decimal
    trigger_price: Decimal
    move_pct: Decimal
    window_change_pct: Decimal | None = None
    peers: tuple[PeerMove, ...] = ()
    attempt: int
    as_of_at: AwareDatetime
    deadline: date | None = None
    documents: tuple[DocumentRow, ...] = ()
    search_hits: tuple[SearchRow, ...] = ()


class CauseAnswer(BaseModel):
    """모델이 내는 것. **판정은 코드가 한다** — 여기는 값만 받는다.

    `frozen`이 아니다. 검증이 근거를 걸러 새 값을 만들 때 `model_copy`가 아니라 생성으로
    끝내지만, `response_format` 스키마를 뽑는 대상이라 다른 `_State`와 설정을 나눠 둔다.
    """

    model_config = ConfigDict(frozen=True)

    found: bool = Field(description="주어진 문서와 검색 결과 안에 이 급변을 설명하는 것이 있는가")
    cause_text: str = Field(default="", description="원인 한 문장. found가 false면 빈 문자열")
    cause_kind: CauseKind = Field(default=CauseKind.UNCLEAR, description="무엇이 방아쇠였나")
    document_ids: tuple[int, ...] = Field(default=(), description="근거로 든 우리 문서의 id")
    search_indexes: tuple[int, ...] = Field(default=(), description="근거로 든 검색 결과의 번호")
