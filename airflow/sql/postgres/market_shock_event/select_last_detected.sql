-- 그 대상의 가장 최근 포착 시각. 쿨다운 판정에 쓴다.
--
-- **자연키만으로는 중복을 못 막는다.** 한 급락이 30분 이어지면 5분 폴링에 여섯 번 걸리는데,
-- 낙폭이 깊어지면서 매번 **다른 봉**이 임계에 닿는다. `(symbol, detected_at)`이 전부 달라
-- `ON CONFLICT`가 통과시킨다. 그래서 쿨다운이 따로 있다.
SELECT detected_at
FROM market_shock_event
WHERE symbol = %(symbol)s
ORDER BY detected_at DESC
LIMIT 1
