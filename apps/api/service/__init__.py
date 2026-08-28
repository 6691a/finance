"""행을 응답 계약으로 바꾸는 층. **계약만 안다.**

행이 어디서 왔는지 모르고 `apps/api/schemas/`의 모양으로 바꾼다. 그래서 Neo4j를 조회
원본으로 채택하면 리포지토리만 갈리고 여기와 계약은 그대로다. 반대로 응답 모양이
바뀌면 여기만 갈린다.

**모양을 바꾸는 함수는 모듈 수준 순수 함수다.** 세션도 리포지토리도 필요 없어 테스트가
직접 부를 수 있고, 클래스는 그것들을 순서대로 엮기만 한다.

**파일은 리소스 단위로 나눈다**(`apps/api/schemas/`와 같은 규칙). `common.py`는 그
서비스들이 공유하는 변환이고, `__init__.py`는 **재수출만** 한다.

**리소스마다 이름이 겹치는 함수는 모듈 경로로 부른다.** `build_list`·`build_detail`이
추론과 실행 둘에 있어 여기서 재수출하지 않는다 — 이름을 하나로 합치면 어느 리소스의
매핑인지가 부르는 자리에서 사라진다.
"""

from apps.api.service.collection import CollectionReadService, build_health, health_of, record_of
from apps.api.service.common import number
from apps.api.service.document import (
    DocumentReadService,
    detail_of,
    disclosure_of,
    earnings_of,
    source_of,
)
from apps.api.service.event import EventReadService, claim_of, signal_of
from apps.api.service.indicator import (
    IndicatorReadService,
    build_curve,
    build_points,
    series_item,
)
from apps.api.service.llm_run import (
    LlmRunReadService,
    item_of,
    narrated_of,
    produced_of,
    run_url,
    tool_call_detail_of,
    tool_call_of,
    tool_call_url,
)
from apps.api.service.positioning import PositioningReadService, flow_of, stock_flow_of
from apps.api.service.quality import (
    QualityReadService,
    beats_uniform,
    build_quality,
    forecast_row_of,
    narrative_row_of,
)
from apps.api.service.quote import (
    QuoteReadService,
    build_bars,
    build_daily,
    build_symbols,
    columns,
    wider,
)
from apps.api.service.thesis import (
    ThesisReadService,
    citation_of,
    llm_run_of,
    outcome_of,
    precedent_of,
    project_graph,
    summary_of,
    thesis_node_id,
)

__all__ = [
    "CollectionReadService",
    "DocumentReadService",
    "EventReadService",
    "IndicatorReadService",
    "LlmRunReadService",
    "PositioningReadService",
    "QualityReadService",
    "QuoteReadService",
    "ThesisReadService",
    "beats_uniform",
    "build_bars",
    "build_curve",
    "build_daily",
    "build_health",
    "build_points",
    "build_quality",
    "build_symbols",
    "citation_of",
    "claim_of",
    "columns",
    "detail_of",
    "disclosure_of",
    "earnings_of",
    "flow_of",
    "forecast_row_of",
    "health_of",
    "item_of",
    "llm_run_of",
    "narrated_of",
    "narrative_row_of",
    "number",
    "outcome_of",
    "precedent_of",
    "produced_of",
    "project_graph",
    "record_of",
    "run_url",
    "series_item",
    "signal_of",
    "source_of",
    "stock_flow_of",
    "summary_of",
    "thesis_node_id",
    "tool_call_detail_of",
    "tool_call_of",
    "tool_call_url",
    "wider",
]
