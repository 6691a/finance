-- 전달 경로 노드 하나를 넣고 id를 돌려준다.
--
-- 자연키가 이름 하나다. **채널에는 날짜가 없다** — `할인율`은 언제 나와도 같은 `할인율`이고,
-- 그 겹침이 서로 다른 주의 경로를 사슬로 잇는다. 이 설계에서 다중 홉이 생기는 유일한 자리다.
--
-- `DO UPDATE`와 `first_seen_week`을 안 덮는 이유는 `market_event/upsert.sql`과 같다.
--
-- 정의의 원본은 `apps/models/analysis/causal.py`의 `MarketChannel`이다.
-- **`first_seen_week`을 함께 돌려준다.** 부르는 쪽이 "이 이름이 이번 주에 처음 생겼나"를
-- 알아야 어휘 수렴을 잴 수 있다. upsert라 이름이 이미 있으면 행이 안 생기는데, 그때는 예전
-- 주가 돌아오므로 `window.week_start`와 비교하면 그대로 판정이 된다.
INSERT INTO market_channel (name, first_seen_week)
VALUES (%s, %s)
ON CONFLICT ON CONSTRAINT uq_market_channel_natural_key
DO UPDATE SET name = EXCLUDED.name
RETURNING id, first_seen_week
