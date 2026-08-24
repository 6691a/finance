-- 판정을 기다리는 실적(earnings) 기대 주장 전부.
--
-- 실적의 실제값은 LLM 주장이 아니라 earnings_fact가 원본이다. 그래서 여기서는 "기대가
-- 있는데 아직 판정이 없는" 키의 기대 행만 모으고, 실제값 존재 여부는 부르는 쪽이 키마다
-- earnings_fact/select_actual_for_judgment.sql로 확인한다.
WITH pending AS (
    SELECT DISTINCT c.stock_code, c.period_key, c.metric
    FROM stock_event_claim c
    WHERE c.claim_kind = 'expectation'
      AND c.event_type = 'earnings'
      AND NOT EXISTS (
          SELECT 1
          FROM stock_event_outcome o
          WHERE o.stock_code = c.stock_code
            AND o.event_type = 'earnings'
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
 AND p.period_key = c.period_key
 AND p.metric = c.metric
WHERE c.claim_kind = 'expectation'
  AND c.event_type = 'earnings'
ORDER BY c.stock_code, c.period_key, c.metric, c.stated_at, c.id
