-- 시장마다 마지막 등락 종목 수 스냅샷 하나. 상한가·하한가는 브리핑에 넣지 않는다.
SELECT DISTINCT ON (symbol)
       symbol,
       observed_at,
       rising_count,
       unchanged_count,
       falling_count
FROM market_movement_snapshot
WHERE observed_at >= %s
ORDER BY symbol, observed_at DESC
