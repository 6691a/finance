-- 추출한 이벤트 주장 한 건. append-only이며 같은 문서의 같은 (이벤트, 지표, 종류) 주장이
-- 다시 와도(본문 갱신 뒤 재추출) 첫 값을 지킨다 — 첫 성공본 불변과 같은 태도다.
-- 정의의 원본은 `apps/models/analysis.py`의 `StockEventClaim`이다.
INSERT INTO stock_event_claim (
    stock_code,
    event_type,
    period_key,
    metric,
    claim_kind,
    value,
    value_low,
    value_high,
    stated_at,
    broker,
    document_id,
    source_record_id
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (document_id, event_type, period_key, metric, claim_kind) DO NOTHING
