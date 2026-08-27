"""API 응답 계약.

**여기가 store 독립성의 경계다.** 조회가 어디서 오든(지금은 Postgres, 나중에 Neo4j)
클라이언트가 보는 모양은 이 패키지가 정한다.

**파일은 리소스 단위로 나눈다**(`apps/models/`가 도메인 단위로 나뉜 것과 같은 규칙).
`common.py`는 그 리소스들이 공유하는 형태 — 공통 베이스와 시각 표기다.

`__init__.py`는 **재수출만** 한다. 부르는 쪽은 `from apps.api.schemas import ThesisDetail`
하나로 끝내고 어느 파일에 있는지 몰라도 된다. 모델을 더할 때 여기 이름을 빠뜨리면
`ruff`가 잡지 못하므로 함께 넣는다.
"""

from apps.api.schemas.common import ApiModel, UtcDatetime
from apps.api.schemas.graph import GraphEdge, GraphNode, GraphResponse
from apps.api.schemas.health import Health
from apps.api.schemas.thesis import (
    EvidenceCitation,
    LlmRunSummary,
    PrecedentRef,
    ThesisDetail,
    ThesisList,
    ThesisOutcomeItem,
    ThesisSummary,
)

__all__ = [
    "ApiModel",
    "EvidenceCitation",
    "GraphEdge",
    "GraphNode",
    "GraphResponse",
    "Health",
    "LlmRunSummary",
    "PrecedentRef",
    "ThesisDetail",
    "ThesisList",
    "ThesisOutcomeItem",
    "ThesisSummary",
    "UtcDatetime",
]
