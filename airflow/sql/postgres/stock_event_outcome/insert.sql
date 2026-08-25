-- 서프라이즈 판정 한 건. 첫 성공본 불변이다 — 같은 (종목, 이벤트, 기간, 지표)에 행이 있으면
-- 아무 것도 바꾸지 않는다. 발표 뒤 기대 행이 늦게 추출돼도 판정을 다시 내지 않는다.
-- RETURNING이 0행이면 이번 실행이 쓴 것이 아니므로 Slack 발송 대상이 아니다.
-- 정의의 원본은 `apps/models/analysis/events.py`의 `StockEventOutcome`이다.
INSERT INTO stock_event_outcome (
    stock_code,
    event_type,
    period_key,
    metric,
    expected_value,
    expectation_count,
    actual_value,
    surprise_pct,
    verdict,
    announced_at,
    actual_ref,
    dag_run_id
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (stock_code, event_type, period_key, metric) DO NOTHING
RETURNING id
