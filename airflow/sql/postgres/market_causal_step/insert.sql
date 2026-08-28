-- 경로의 전달 단계 하나. `position`이 1부터 사건 쪽에서 대상 쪽으로 는다.
--
-- **헤더와 같은 트랜잭션에 쓴다.** 경로만 들어가고 단계가 빠진 상태를 남기지 않는다 —
-- 그러면 `chain_key`가 가리키는 것이 DB에 없다.
--
-- `ON CONFLICT DO NOTHING`은 같은 트랜잭션이 실패해 재시도할 때를 위한 것이다.
--
-- 정의의 원본은 `apps/models/analysis/causal.py`의 `MarketCausalStep`이다.
INSERT INTO market_causal_step (path_id, position, channel_id)
VALUES (%s, %s, %s)
ON CONFLICT ON CONSTRAINT uq_market_causal_step_natural_key DO NOTHING
