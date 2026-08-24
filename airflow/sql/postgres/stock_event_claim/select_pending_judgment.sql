-- 판정을 기다리는 이벤트 키의 주장 전부(기대와 실제 함께).
--
-- "실제(actual) 주장이 하나라도 있는데 아직 판정 행이 없는" (종목, 이벤트, 기간, 지표)가
-- 대상이다. 실적(earnings)의 실제값은 earnings_fact가 원본이라 여기 안 들어온다 —
-- 그쪽은 select_pending_earnings_expectations.sql이 따로 모은다.
--
-- 대표 기대치 집계(컨센서스 우선, 주체별 최신, 발표 전 컷)는 순수 함수가 한다. SQL은
-- 행을 모으기만 한다 — DB 없이 경계값을 테스트하기 위해서다(채점 수식과 같은 이유).
WITH pending AS (
    SELECT DISTINCT c.stock_code, c.event_type, c.period_key, c.metric
    FROM stock_event_claim c
    WHERE c.claim_kind = 'actual'
      AND c.event_type <> 'earnings'
      AND NOT EXISTS (
          SELECT 1
          FROM stock_event_outcome o
          WHERE o.stock_code = c.stock_code
            AND o.event_type = c.event_type
            AND o.period_key = c.period_key
            AND o.metric = c.metric
      )
)
SELECT
    c.stock_code,
    c.event_type,
    c.period_key,
    c.metric,
    c.claim_kind,
    c.value,
    c.stated_at,
    c.broker,
    c.document_id,
    c.source_record_id
FROM stock_event_claim c
JOIN pending p
  ON p.stock_code = c.stock_code
 AND p.event_type = c.event_type
 AND p.period_key = c.period_key
 AND p.metric = c.metric
ORDER BY c.stock_code, c.event_type, c.period_key, c.metric, c.stated_at, c.id
