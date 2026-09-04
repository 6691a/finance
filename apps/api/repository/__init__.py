"""행을 읽는 층. **store만 안다.**

응답 계약(`apps/api/schemas/`)을 모르고 행 묶음만 돌려준다 — 모양을 바꾸는 것은
`apps/api/service/`다. 그래야 저장소를 갈아끼울 때 이 패키지만 갈리고 매핑과 계약은
그대로다.

**파일은 리소스 단위로 나눈다**(`apps/api/schemas/`와 같은 규칙). `common.py`는 그
리포지토리들이 공유하는 것이고 `graph.py`는 Neo4j에 붙는 공통 조각이다.
`__init__.py`는 **재수출만** 한다.
"""

from apps.api.repository.collection import CollectionReadRepository
from apps.api.repository.common import (
    DEFAULT_LIMIT,
    DEFAULT_WINDOW_DAYS,
    MAX_LIMIT,
    RowBundle,
    page_slice,
)
from apps.api.repository.document import (
    DocumentDetailRows,
    DocumentListRows,
    DocumentReadRepository,
    SourceRows,
)
from apps.api.repository.event import EventReadRepository
from apps.api.repository.forecast import (
    FORECAST_SLOTS,
    AccuracyRow,
    ForecastListRows,
    ForecastReadRepository,
)
from apps.api.repository.graph import Driver, UnsafeCypher, as_date, ensure_read_only
from apps.api.repository.indicator import (
    CURVE_KIND,
    CurveRows,
    IndicatorReadRepository,
    IndicatorSeriesRows,
)
from apps.api.repository.kospi_graph import (
    RELATION_LOOKBACK_DAYS,
    KospiGraphReadRepository,
    MemoryRow,
    ObservationRow,
)
from apps.api.repository.llm_run import (
    LLM_RUN_STATUSES,
    LlmRunDetailRows,
    LlmRunListRows,
    LlmRunReadRepository,
)
from apps.api.repository.positioning import PositioningReadRepository
from apps.api.repository.quality import (
    ForecastGrade,
    QualityReadRepository,
    QualityRows,
    ReviewStat,
)
from apps.api.repository.quote import (
    BAR_TABLES,
    DAILY_TABLES,
    INTERVALS,
    MAX_POINTS,
    QuoteReadRepository,
    SymbolRows,
    bar_provider,
)

__all__ = [
    "BAR_TABLES",
    "CURVE_KIND",
    "DAILY_TABLES",
    "DEFAULT_LIMIT",
    "DEFAULT_WINDOW_DAYS",
    "FORECAST_SLOTS",
    "INTERVALS",
    "LLM_RUN_STATUSES",
    "MAX_LIMIT",
    "MAX_POINTS",
    "RELATION_LOOKBACK_DAYS",
    "AccuracyRow",
    "CollectionReadRepository",
    "CurveRows",
    "DocumentDetailRows",
    "DocumentListRows",
    "DocumentReadRepository",
    "Driver",
    "EventReadRepository",
    "ForecastGrade",
    "ForecastListRows",
    "ForecastReadRepository",
    "IndicatorReadRepository",
    "IndicatorSeriesRows",
    "KospiGraphReadRepository",
    "LlmRunDetailRows",
    "LlmRunListRows",
    "LlmRunReadRepository",
    "MemoryRow",
    "ObservationRow",
    "PositioningReadRepository",
    "QualityReadRepository",
    "QualityRows",
    "QuoteReadRepository",
    "ReviewStat",
    "RowBundle",
    "SourceRows",
    "SymbolRows",
    "UnsafeCypher",
    "as_date",
    "bar_provider",
    "ensure_read_only",
    "page_slice",
]
