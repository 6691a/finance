-- 추론 툴 `event_surprises`가 쓴다. **아직 발표되지 않은** 이벤트의 기대치를 준다.
--
-- 장전 추론이 "오늘 발표가 나오면 기준선이 얼마인가"를 알아야 서프라이즈를 해석한다.
-- 판정 행이 없는 (이벤트, 기간, 지표)의 기대 주장만 모아 대표값을 낸다.
--
-- 대표값은 여기서 중앙값으로 낸다. 판정 경로(`select_pending_judgment.sql`)가 행을 그대로
-- 주고 순수 함수가 집계하는 것과 다른데, 저쪽은 저장할 값이라 규칙(컨센서스 우선, 주체별
-- 최신)을 테스트 가능한 코드에 둬야 하고 이쪽은 모델에게 읽히는 문맥이기 때문이다.
-- 주체별 최신을 여기서도 적용해 같은 증권사의 옛 기대가 중앙값을 끌지 않게 한다.
--
-- 창의 끝은 `stated_at`이다 — 그 시점까지 나온 기대만 본다.
--
-- 주석에 퍼센트 기호를 쓰지 않는다. psycopg가 주석까지 훑어 플레이스홀더로 센다.
WITH latest_per_broker AS (
    SELECT DISTINCT ON (claim.event_type, claim.period_key, claim.metric, claim.broker)
           claim.event_type,
           claim.period_key,
           claim.metric,
           claim.broker,
           claim.value,
           claim.stated_at
    FROM stock_event_claim AS claim
    WHERE claim.stock_code = %(stock_code)s
      AND claim.claim_kind = 'expectation'
      AND claim.stated_at <= %(as_of_at)s
      AND NOT EXISTS (
          SELECT 1
          FROM stock_event_outcome AS outcome
          WHERE outcome.stock_code = %(stock_code)s
            AND outcome.event_type = claim.event_type
            AND outcome.period_key = claim.period_key
            AND outcome.metric = claim.metric
      )
    ORDER BY claim.event_type, claim.period_key, claim.metric, claim.broker,
             claim.stated_at DESC, claim.id DESC
)
SELECT event_type,
       period_key,
       metric,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY value) AS expected_value,
       count(*) AS expectation_count,
       max(stated_at) AS latest_stated_at
FROM latest_per_broker
GROUP BY event_type, period_key, metric
ORDER BY max(stated_at) DESC
LIMIT %(limit)s
