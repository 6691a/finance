"""후보 조립 — 프롬프트에 실을 것을 코드가 먼저 좁힌다.

주당 평가 문서가 1,000건을 넘어(2026-08-27 실측) 전부 실을 수 없다. 여기서 50건 안팎으로
좁히고, 모자라면 모델이 툴로 더 판다(설계 §5.1·§5.2).

**LangChain을 import하지 않는다.** 이 모듈이 아는 것은 연결과 SQL뿐이다.
"""

from collections.abc import Iterable, Sequence
from datetime import datetime, time, timedelta

from modules.causal.domain import (
    EVENT_LOOKBACK_WEEKS,
    INDEX_TARGETS,
    INDICATOR_TARGETS,
    MACRO_TARGETS,
    CandidateSet,
    CausalReturnUnit,
    CausalTarget,
    CausalTargetKind,
    CausalWindow,
    ChannelOption,
    DisclosureCandidate,
    DocumentCandidate,
    EventOption,
    SignalCandidate,
    TargetReturns,
)
from modules.db import Connection
from modules.sql import read_sql
from modules.utility import KST_TIMEZONE

WATCHED_STOCKS = read_sql("postgres", "causal", "select_watched_stocks.sql")

# 대상 종류마다 원본 테이블이 다르다. 한 쿼리로 묶지 않는 이유는 각 SQL 머리에 있다.
RETURNS_SQL: dict[CausalTargetKind, str] = {
    CausalTargetKind.INDEX: read_sql("postgres", "causal", "select_index_returns.sql"),
    CausalTargetKind.INSTRUMENT: read_sql("postgres", "causal", "select_stock_returns.sql"),
    CausalTargetKind.QUOTE: read_sql("postgres", "causal", "select_quote_returns.sql"),
    CausalTargetKind.INDICATOR: read_sql("postgres", "causal", "select_indicator_returns.sql"),
}

# 실현 등락은 금리만 bp다. 나머지는 종가 대비 변화율이다.
RETURN_UNITS: dict[CausalTargetKind, CausalReturnUnit] = {
    CausalTargetKind.INDEX: CausalReturnUnit.PERCENT,
    CausalTargetKind.INSTRUMENT: CausalReturnUnit.PERCENT,
    CausalTargetKind.QUOTE: CausalReturnUnit.PERCENT,
    CausalTargetKind.INDICATOR: CausalReturnUnit.BASIS_POINT,
}

# 실현 등락 조회의 끝. 반응 주 금요일이 아니라 넉넉히 잡아야 휴장이 겹쳐도 T+5가 잡힌다.
RETURNS_SCAN_DAYS = 15

RECENT_EVENTS = read_sql("postgres", "market_event", "select_recent.sql")
ALL_CHANNELS = read_sql("postgres", "market_channel", "select_all.sql")

DOCUMENTS = read_sql("postgres", "causal", "select_documents.sql")
DISCLOSURES = read_sql("postgres", "causal", "select_disclosures.sql")
SIGNALS = read_sql("postgres", "causal", "select_signals.sql")

# 문서 후보 상한. **대상별이 아니라 그 주 전체에서 상위 몇 건이다**(2026-08-28 정정).
# 대상별로 뽑으면 대상 목록 밖 지표에만 태그된 문서가 통째로 빠진다.
# 8주 프로토타입이 32~50건, 운영 첫 실행이 61건으로 잘 돌았다.
MAX_DOCUMENTS = 60

# 한 소스가 후보에서 가져갈 수 있는 최대 건수. 근거는 SQL 머리에 있다 — 점수순으로만
# 자르면 두꺼운 소스가 자리를 독식해 원천 통계와 중앙은행 발표가 통째로 빠진다.
#
# **4는 커버리지와 두께의 균형점이다**(2026-08-28 8/17 주 실측). 8이면 소스 열둘,
# 상한 없이 순번만 돌리면 열아홉이 남지만 1점짜리까지 들어온다. 4가 열여덟에 최저 3점이다.
# 소스가 더 늘면 이 값을 내려야 한다 — 소스 수 × 이 값이 `MAX_DOCUMENTS`를 크게 넘으면
# 상한이 다시 점수순 절단으로 돌아간다.
MAX_DOCUMENTS_PER_SOURCE = 4


def resolve_targets(connection: Connection) -> tuple[CausalTarget, ...]:
    """이 실행이 다룰 대상. 지수 둘 → 관심종목 → 매크로 다섯 → 금리 둘 순서다.

    **종목만 마스터에서 읽는다.** 관심종목을 늘리면 대상이 따라 늘고, 그만큼 후보와 비용도
    는다. 지수·매크로는 늘어나는 목록이 아니라 상수다(설계 §0).
    """
    with connection.cursor() as cursor:
        cursor.execute(WATCHED_STOCKS)
        stocks = tuple(
            CausalTarget(kind=CausalTargetKind.INSTRUMENT, code=row[0])
            for row in cursor.fetchall()
        )
    return INDEX_TARGETS + stocks + MACRO_TARGETS + INDICATOR_TARGETS


def fetch_returns(
    connection: Connection,
    targets: Iterable[CausalTarget],
    window: CausalWindow,
) -> dict[str, TargetReturns]:
    """대상별 실현 등락. **값이 온전한 대상만 담는다.**

    셋 중 하나라도 없으면 그 대상을 빼는 이유는 설계 §6이다 — NULL로 저장하면 "안 쟀다"와
    "잴 수 없었다"가 나중에 구분되지 않는다. 반응 주가 아직 안 끝났거나 그 계열의 수집이
    늦게 시작된 주가 그렇고, 둘 다 정상 흐름이라 실패로 만들지 않는다.
    """
    by_kind: dict[CausalTargetKind, list[str]] = {}
    providers: dict[CausalTargetKind, set[str]] = {}
    for target in targets:
        by_kind.setdefault(target.kind, []).append(target.code)
        if target.provider is not None:
            providers.setdefault(target.kind, set()).add(target.provider)

    returns: dict[str, TargetReturns] = {}
    for kind, codes in by_kind.items():
        for provider in sorted(providers.get(kind, {""})):
            returns |= _fetch_kind(connection, kind, codes, provider, window)
    return returns


def _fetch_kind(
    connection: Connection,
    kind: CausalTargetKind,
    codes: Sequence[str],
    provider: str,
    window: CausalWindow,
) -> dict[str, TargetReturns]:
    parameters = {
        "codes": list(codes),
        "week_start": window.week_start,
        "week_end": window.week_end,
        "scan_end": window.week_end + timedelta(days=RETURNS_SCAN_DAYS),
    }
    if provider:
        parameters["provider"] = provider
    with connection.cursor() as cursor:
        cursor.execute(RETURNS_SQL[kind], parameters)
        rows = cursor.fetchall()
    unit = RETURN_UNITS[kind]
    return {
        row[0]: TargetReturns(week=row[1], t1=row[2], t5=row[3], unit=unit)
        for row in rows
        if row[1] is not None and row[2] is not None and row[3] is not None
    }


def fetch_candidates(
    connection: Connection,
    targets: Iterable[CausalTarget],
    window: CausalWindow,
) -> CandidateSet:
    """프롬프트에 실을 근거 후보. **매크로 변화는 여기 없다** — 이제 대상이라
    `fetch_returns`가 실현 등락으로 준다.

    셋 다 `as_of_at` cutoff를 건다. 근거는 "그 시점에 알 수 있었던 것"이어야 한다 —
    실현 등락과 반대다.
    """
    codes = [target.code for target in targets]
    week_start_at = datetime.combine(window.week_start, time.min, tzinfo=KST_TIMEZONE)
    week_after_at = datetime.combine(
        window.week_end + timedelta(days=3), time.min, tzinfo=KST_TIMEZONE
    )

    with connection.cursor() as cursor:
        cursor.execute(
            DOCUMENTS,
            {
                "codes": codes,
                "week_start_at": week_start_at,
                "week_after_at": week_after_at,
                "as_of_at": window.as_of_at,
                "limit": MAX_DOCUMENTS,
                "per_source": MAX_DOCUMENTS_PER_SOURCE,
            },
        )
        documents = tuple(
            DocumentCandidate(
                ref=f"document:{row[0]}",
                title=row[1],
                summary=row[2] or "",
                source_slug=row[3],
                published_at=row[4],
                value_score=row[5],
                assessed_direction=row[6],
                tags=tuple(row[7] or ()),
            )
            for row in cursor.fetchall()
        )

    with connection.cursor() as cursor:
        cursor.execute(
            DISCLOSURES,
            {
                "codes": codes,
                "week_start": window.week_start,
                "week_end": window.week_end,
                "as_of_at": window.as_of_at,
            },
        )
        disclosures = tuple(
            DisclosureCandidate(
                ref=f"disclosure:{row[1]}",
                target_code=row[0],
                company_name=row[2],
                report_name=row[3],
                receipt_date=row[4],
                body=row[5],
            )
            for row in cursor.fetchall()
        )

    with connection.cursor() as cursor:
        cursor.execute(
            SIGNALS,
            {
                "codes": codes,
                "week_start": window.week_start,
                "week_end": window.week_end,
                "as_of_at": window.as_of_at,
            },
        )
        signals = tuple(
            SignalCandidate(
                ref=f"technical_signal:{row[0]}",
                target_code=row[1],
                signal_date=row[2],
                kind=row[3],
                direction=row[4],
            )
            for row in cursor.fetchall()
        )

    return CandidateSet(documents=documents, disclosures=disclosures, signals=signals)


def fetch_vocabulary(
    connection: Connection,
    window: CausalWindow,
) -> tuple[tuple[EventOption, ...], tuple[ChannelOption, ...]]:
    """프롬프트에 후보로 실을 어휘. **이것이 서로 다른 주의 그래프를 잇는다**(설계 §4).

    **사건과 경로에 다른 장치를 쓰지 않는다.** 둘 다 좁힐 방법이 있어서다 — 경로는 수렴해서
    좁고, 사건은 날짜로 좁는다. 임베딩도 사후 병합도 두지 않는 근거가 그것이다.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            RECENT_EVENTS,
            {
                "since": window.week_start - timedelta(weeks=EVENT_LOOKBACK_WEEKS),
                "until": window.week_end,
            },
        )
        events = tuple(
            EventOption(node_id=f"e:{row[0]}", title=row[1], occurred_on=row[2])
            for row in cursor.fetchall()
        )

    with connection.cursor() as cursor:
        cursor.execute(ALL_CHANNELS)
        channels = tuple(
            ChannelOption(node_id=f"c:{row[0]}", name=row[1]) for row in cursor.fetchall()
        )
    return events, channels
