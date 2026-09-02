"""투영에 오가는 행 모델. **neo4j도 Postgres도 import하지 않는다.**

`causal/domain.py`가 `causal/generation.py`와 갈린 것과 같은 자리다 — 값의 모양은 그것을
읽고 쓰는 드라이버보다 훨씬 자주 바뀌고, 바뀌는 이유도 다르다(Postgres 조회의 컬럼 순서).
여기를 따로 두면 모양만 보는 쪽이 드라이버를 물지 않는다.
"""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class _Row(BaseModel):
    """투영에 오가는 값은 전부 불변이다. 재시도 경로에서 바뀌면 원본과 어긋난다."""

    model_config = ConfigDict(frozen=True)


class CausalPathRow(_Row):
    """`market_causal_path` 한 행 중 그래프에 싣는 것만."""

    path_id: int
    week_start: date
    created_at: datetime
    event_title: str | None
    event_occurred_on: date | None
    source_target_kind: str | None
    source_target_code: str | None
    source_sign: str | None
    target_kind: str
    target_code: str
    sign: str
    confidence: str
    reasoning: str
    return_week_change: float
    return_t1_change: float
    return_t5_change: float
    return_unit: str


class CausalStepRow(_Row):
    """`market_causal_step` 한 행. 채널을 id가 아니라 이름으로 갖는다."""

    path_id: int
    position: int
    channel: str


class EventNode(_Row):
    title: str
    occurred_on: date


class ChannelNode(_Row):
    name: str


class TargetNode(_Row):
    kind: str
    code: str


class EventEdge(_Row):
    """사건에서 첫 채널로."""

    path_id: int
    week_start: date
    created_at: datetime
    position: int
    title: str
    occurred_on: date
    channel: str


class TargetEdge(_Row):
    """대상에서 첫 채널로. 앞 주의 결과가 다음 원인이 되는 자리다(설계 §11.4)."""

    path_id: int
    week_start: date
    created_at: datetime
    position: int
    src_kind: str
    src_code: str
    sign: str
    channel: str


class ChainEdge(_Row):
    """채널에서 채널로."""

    path_id: int
    week_start: date
    created_at: datetime
    position: int
    src: str
    dst: str


class HitsEdge(_Row):
    """마지막 채널에서 대상으로. **경로 수준 속성이 여기 실린다** — 주장이 착지하는 자리다."""

    path_id: int
    week_start: date
    created_at: datetime
    channel: str
    kind: str
    code: str
    sign: str
    confidence: str
    reasoning: str
    return_unit: str
    return_week_change: float
    return_t1_change: float
    return_t5_change: float


class GraphPayload(_Row):
    """한 주를 Neo4j에 넣을 모양으로 편 것."""

    events: tuple[EventNode, ...]
    channels: tuple[ChannelNode, ...]
    targets: tuple[TargetNode, ...]
    from_event: tuple[EventEdge, ...]
    from_target: tuple[TargetEdge, ...]
    chain: tuple[ChainEdge, ...]
    hits: tuple[HitsEdge, ...]

    @property
    def node_count(self) -> int:
        return len(self.events) + len(self.channels) + len(self.targets)

    @property
    def edge_count(self) -> int:
        return len(self.from_event) + len(self.from_target) + len(self.chain) + len(self.hits)
