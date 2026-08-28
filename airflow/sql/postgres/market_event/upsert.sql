-- 사건 노드 하나를 넣고 id를 돌려준다. 테이블은 백엔드 마이그레이션이 만든다.
--
-- **`DO UPDATE`인 이유는 id가 필요해서다.** `DO NOTHING`은 충돌 시 0행을 돌려줘서 부르는
-- 쪽이 SELECT를 한 번 더 해야 한다. 여기서는 갱신할 것이 없으므로 자기 값을 다시 넣는
-- no-op 갱신으로 `RETURNING`을 살린다.
--
-- **`first_seen_week`은 안 덮는다.** 어휘가 언제 자랐는지를 보는 값이라 최초 주가 원본이다.
--
-- 자연키가 `(title, occurred_on)`인 이유는 같은 제목이 다른 날 다시 일어나면 다른 사건이기
-- 때문이다 — `미국 반도체 지수 하락`이 8주 프로토타입에서 두 번 나왔다.
--
-- 정의의 원본은 `apps/models/analysis/causal.py`의 `MarketEvent`다.
INSERT INTO market_event (title, occurred_on, first_seen_week)
VALUES (%s, %s, %s)
ON CONFLICT ON CONSTRAINT uq_market_event_natural_key
DO UPDATE SET title = EXCLUDED.title
RETURNING id
