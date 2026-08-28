-- 다음 실행의 프롬프트에 후보로 실을 최근 사건.
--
-- **사건은 수렴하지 않는다.** 매주 새 사건이 생기므로 목록이 계속 커진다. 대신 날짜가
-- 붙어 있어 좁힐 수 있다 — 지난달 사건과 이번 주 사건이 같은 것일 가능성은 낮다.
-- 몇 주를 거슬러 볼지는 `causal/domain.py`의 `EVENT_LOOKBACK_WEEKS`가 정한다.
-- **여기에 숫자를 적지 마라** — 두 곳에 적으면 반드시 어긋난다.
--
-- `occurred_on` 역순이다. 프롬프트에서 최근 것이 먼저 보이는 편이 낫다.
--
-- 정의의 원본은 `apps/models/analysis/causal.py`의 `MarketEvent`다.
SELECT id, title, occurred_on
FROM market_event
WHERE occurred_on >= %(since)s
  AND occurred_on <= %(until)s
ORDER BY occurred_on DESC, id
