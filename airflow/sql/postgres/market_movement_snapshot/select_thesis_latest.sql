-- 추론 툴 `market_breadth`가 쓴다. 시장마다 `as_of_at` 이전 마지막 등락 종목 수 하나.
--
-- 상한가·하한가도 준다. 브리핑은 뺐지만(자리가 좁다) 모델에게는 "지수는 조금 올랐는데
-- 하한가가 여럿"처럼 지수만으로 안 보이는 것을 짚을 재료가 된다.
--
-- 창의 끝은 `observed_at`이다. 장중 스냅샷이라 관측 시각이 곧 event time이다.
SELECT DISTINCT ON (symbol)
       symbol,
       observed_at,
       rising_count,
       unchanged_count,
       falling_count,
       upper_limit_count,
       lower_limit_count
FROM market_movement_snapshot
WHERE observed_at >= %(window_start)s
  AND observed_at <= %(as_of_at)s
ORDER BY symbol, observed_at DESC
