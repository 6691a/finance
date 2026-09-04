"""추론·이벤트·기술적 신호 모델.

**이 파일이 등록 경로다.** 테이블 등록은 클래스를 import하는 부수효과이므로 여기서
이름을 빠뜨리면 `Base.metadata`에서 그 테이블이 사라지고 autogenerate가 `DROP`을 낸다.
`tests/models/test_analysis_models.py`가 하위 모듈을 훑어 그 누락을 잡는다.
"""

from apps.models.analysis.events import (
    EVENT_METRICS,
    PERIOD_KEY_PATTERN,
    StockEventClaim,
    StockEventClaimKind,
    StockEventExtraction,
    StockEventMetric,
    StockEventOutcome,
    StockEventType,
    SurpriseVerdict,
)
from apps.models.analysis.kospi import (
    KospiDirection,
    KospiForecast,
    KospiLlmRun,
    KospiLlmRunKind,
    KospiLlmRunStatus,
    KospiSlot,
    KospiToolCall,
    KospiToolCallErrorKind,
)
from apps.models.analysis.technical import (
    SignalDirection,
    TechnicalSignal,
    TechnicalSignalKind,
)

__all__ = [
    "EVENT_METRICS",
    "PERIOD_KEY_PATTERN",
    "KospiDirection",
    "KospiForecast",
    "KospiLlmRun",
    "KospiLlmRunKind",
    "KospiLlmRunStatus",
    "KospiSlot",
    "KospiToolCall",
    "KospiToolCallErrorKind",
    "SignalDirection",
    "StockEventClaim",
    "StockEventClaimKind",
    "StockEventExtraction",
    "StockEventMetric",
    "StockEventOutcome",
    "StockEventType",
    "SurpriseVerdict",
    "TechnicalSignal",
    "TechnicalSignalKind",
]
