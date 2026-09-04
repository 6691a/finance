"""관계와 메모의 매핑. **감쇠 평균이 여기 있다.**

## 왜 같은 식이 두 곳에 있나

가중치의 원본은 `airflow/modules/kospi/domain.relation_weight`이고, 그 값이 프롬프트 표에
실린다. 그런데 **`apps/`는 `airflow/`를 import하지 않는다**(저장소 규칙) — Airflow 트리는
컨테이너에서 `/opt/airflow`로 마운트되고 이쪽은 그 경로를 보지 못한다.

그래서 중복을 허용하고 **테스트로 대조한다**(`tests/api/test_relation_weight.py`).
`apps/realtime/`이 수집기 상수를 그렇게 다루는 것과 같은 형태다. 상수 셋이 어긋나면
화면과 프롬프트가 같은 요인에 다른 값을 말한다.
"""

from collections.abc import Sequence
from datetime import date

from apps.api.repository import (
    KospiGraphReadRepository,
    MemoryRow,
    ObservationRow,
    page_slice,
)
from apps.api.schemas import (
    MemoryItem,
    MemoryList,
    ObservationItem,
    ObservationList,
    RelationEdge,
    RelationGraph,
    RelationItem,
    RelationList,
    RelationNode,
)

# --- Airflow 쪽과 같아야 하는 값들 -------------------------------------------
# 원본은 `airflow/modules/kospi/domain.py`다. 테스트가 이름과 값을 대조한다.

# 관측의 반감기(달력일). 5일 전 관측이 오늘의 절반 무게다.
RELATION_HALF_LIFE_DAYS = 5

# 가중치에 넣는 최근 관측 수.
RELATION_WINDOW = 15

# 감쇠 없이 그대로 보이는 최근 부호 수.
RECENT_SIGN_COUNT = 3

# 세기의 최대. 가중치를 -1~1로 정규화하는 분모다.
MAX_STRENGTH = 3

# 요인 코드와 한국어 이름. `domain.FACTORS`의 `code`·`label`과 같아야 한다.
FACTOR_LABELS: dict[str, str] = {
    "FOREIGN_NET_BUY": "외국인 순매수",
    "INSTITUTION_NET_BUY": "기관 순매수",
    "INDIVIDUAL_NET_BUY": "개인 순매수",
    "US10Y": "미국 10년물",
    "KTB10Y": "국고채 10년",
    "KRBASE": "한국은행 기준금리",
    "USDKRW": "원달러 환율",
    "DXY": "달러인덱스",
    "SP500": "S&P500",
    "NASDAQ": "나스닥",
    "SOX": "필라델피아 반도체",
    "VIX": "VIX",
    "WTI": "WTI 유가",
    "SAMSUNG": "삼성전자",
    "SK_HYNIX": "SK하이닉스",
    "NEWS": "뉴스",
    "DISCLOSURE": "공시",
}

# 관측이 같은 방향이었음을 뜻하는 값. 아니면 반대로 친다.
SAME = "same"

INDEX_NODE_ID = "index:KOSPI"
INDEX_LABEL = "코스피"

# ---------------------------------------------------------------------------


def relation_url(factor: str) -> str:
    return f"/api/relations/{factor}"


def run_url(llm_run_id: int | None) -> str | None:
    return None if llm_run_id is None else f"/api/llm-runs/{llm_run_id}"


def decay_weight(
    observed_on: date, as_of_date: date, *, half_life_days: int = RELATION_HALF_LIFE_DAYS
) -> float:
    """관측 하나의 무게. 나이가 반감기의 배수만큼 지날 때마다 절반이 된다.

    미래 관측은 무게 1이다 — 그런 행이 오면 조회가 잘못된 것이고, 여기서 조용히 키우거나
    줄이면 그 결함이 숨는다.
    """
    age = max((as_of_date - observed_on).days, 0)
    return 0.5 ** (age / half_life_days)


def fold(
    factor: str,
    observations: Sequence[ObservationRow],
    *,
    as_of_date: date,
    half_life_days: int = RELATION_HALF_LIFE_DAYS,
    window: int = RELATION_WINDOW,
) -> RelationItem:
    """관측들을 요인 하나의 가중치로 접는다. **최신이 무겁다.**

    입력이 최신순이라고 가정하지 않는다 — 여기서 정렬한다. 조회가 순서를 바꿔도 값이
    흔들리지 않아야 한다.

    관측이 없으면 `weight`가 0이고 `n_obs`가 0이다. **그 둘을 가르는 것은 읽는 쪽의
    일이다** — 0은 "관계가 없다"가 아니라 "아직 모른다"이고, 화면이 그 뜻을 밝힌다.
    """
    label = FACTOR_LABELS.get(factor, factor)
    recent = sorted(observations, key=lambda row: row.observed_on, reverse=True)[:window]
    if not recent:
        return RelationItem(factor=factor, label=label, weight=0.0, n_obs=0, url=relation_url(factor))

    numerator = 0.0
    denominator = 0.0
    for row in recent:
        weight = decay_weight(row.observed_on, as_of_date, half_life_days=half_life_days)
        signed = row.strength if row.sign == SAME else -row.strength
        numerator += weight * signed
        denominator += weight * MAX_STRENGTH
    return RelationItem(
        factor=factor,
        label=label,
        weight=round(numerator / denominator, 3) if denominator else 0.0,
        n_obs=len(recent),
        last_date=recent[0].observed_on,
        last_note=recent[0].note,
        recent_signs=tuple(row.sign for row in recent[:RECENT_SIGN_COUNT]),
        url=relation_url(factor),
    )


def group_by_factor(
    observations: Sequence[ObservationRow],
) -> dict[str, list[ObservationRow]]:
    grouped: dict[str, list[ObservationRow]] = {}
    for row in observations:
        grouped.setdefault(row.factor, []).append(row)
    return grouped


def build_relations(
    observations: Sequence[ObservationRow], *, as_of_date: date
) -> tuple[RelationItem, ...]:
    """요인 **전부**를 준다. 관측이 없는 요인도 행이 있고 `n_obs`가 0이다.

    정렬은 |가중치| 내림차순이고 관측 없는 요인이 뒤로 간다. 화면의 첫 줄이 "지금 가장
    세게 작용하는 것"이어야 한다.
    """
    grouped = group_by_factor(observations)
    # 어휘에 없는 요인이 그래프에 있으면 함께 싣는다 — 조용히 숨기면 어휘가 갈린 것을 못 본다.
    codes = list(FACTOR_LABELS) + [code for code in grouped if code not in FACTOR_LABELS]
    items = [fold(code, grouped.get(code, ()), as_of_date=as_of_date) for code in codes]
    return tuple(sorted(items, key=lambda item: (item.n_obs == 0, -abs(item.weight), item.factor)))


def observation_of(row: ObservationRow, *, as_of_date: date) -> ObservationItem:
    return ObservationItem(
        factor=row.factor,
        observed_on=row.observed_on,
        sign=row.sign,
        strength=row.strength,
        note=row.note,
        weight=round(decay_weight(row.observed_on, as_of_date), 4),
        llm_run_id=row.llm_run_id,
        llm_run_url=run_url(row.llm_run_id),
    )


def build_graph(relations: Sequence[RelationItem], *, as_of_date: date) -> RelationGraph:
    """별 모양 하나. **깊이가 1이라 레이아웃이 단순하다** — 요인이 코스피를 가리킨다."""
    nodes = [RelationNode(id=INDEX_NODE_ID, kind="index", label=INDEX_LABEL)]
    edges: list[RelationEdge] = []
    for item in relations:
        node_id = f"factor:{item.factor}"
        nodes.append(
            RelationNode(id=node_id, kind="factor", label=item.label, n_obs=item.n_obs)
        )
        if item.n_obs:
            edges.append(
                RelationEdge(
                    source=node_id,
                    target=INDEX_NODE_ID,
                    weight=item.weight,
                    n_obs=item.n_obs,
                )
            )
    return RelationGraph(as_of_date=as_of_date, nodes=tuple(nodes), edges=tuple(edges))


def memory_of(row: MemoryRow) -> MemoryItem:
    return MemoryItem(
        id=row.id,
        created_on=row.created_on,
        text=row.text,
        factor=row.factor,
        verify_count=row.verify_count,
        unreviewed_count=row.unreviewed_count,
        last_verified_on=row.last_verified_on,
        retired_on=row.retired_on,
        retire_reason=row.retire_reason,
        llm_run_id=row.llm_run_id,
        llm_run_url=run_url(row.llm_run_id),
    )


class RelationReadService:
    """관계·관측·그림·메모를 읽어 응답 계약으로 준다.

    **그래프가 없으면 `enabled`가 False다.** 라우트가 그때 503을 낸다 — 빈 목록으로
    위장하면 "관측이 없다"와 "저장소가 없다"가 같아 보인다.
    """

    def __init__(self, repository: KospiGraphReadRepository) -> None:
        self._repository = repository

    @property
    def enabled(self) -> bool:
        return self._repository.enabled

    def relations(self, *, as_of_date: date, limit: int, offset: int) -> RelationList:
        items = build_relations(self._repository.observations(as_of_date=as_of_date), as_of_date=as_of_date)
        page, has_more = page_slice(items[offset:], limit)
        return RelationList(items=page, limit=limit, offset=offset, has_more=has_more)

    def observations(
        self, factor: str, *, as_of_date: date, limit: int, offset: int
    ) -> ObservationList:
        rows = [
            row
            for row in self._repository.observations(as_of_date=as_of_date)
            if row.factor == factor
        ]
        page, has_more = page_slice(rows[offset:], limit)
        return ObservationList(
            items=tuple(observation_of(row, as_of_date=as_of_date) for row in page),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    def graph(self, *, as_of_date: date) -> RelationGraph:
        relations = build_relations(
            self._repository.observations(as_of_date=as_of_date), as_of_date=as_of_date
        )
        return build_graph(relations, as_of_date=as_of_date)

    def memories(self, *, retired: bool | None, limit: int, offset: int) -> MemoryList:
        rows = self._repository.memories()
        if retired is not None:
            rows = tuple(row for row in rows if (row.retired_on is not None) is retired)
        page, has_more = page_slice(rows[offset:], limit)
        return MemoryList(
            items=tuple(memory_of(row) for row in page),
            limit=limit,
            offset=offset,
            has_more=has_more,
        )

    def memory(self, memory_id: int) -> MemoryItem | None:
        for row in self._repository.memories():
            if row.id == memory_id:
                return memory_of(row)
        return None
