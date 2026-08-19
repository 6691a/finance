-- 장중 차트가 당일 정규장 분봉 종가를 심볼별로 읽는다.
-- provider와 symbol을 각각 ANY로 거르므로 목록에 없는 (provider, symbol) 조합이
-- 섞여 나올 수 있다. 짝 맞추기는 파이썬(CHART_SYMBOLS 순회)이 한다.
SELECT bar.provider,
       bar.symbol,
       symbol.label,
       bar.bar_at,
       bar.close
FROM quote_bar AS bar
JOIN quote_symbol AS symbol
  ON symbol.provider = bar.provider
 AND symbol.symbol = bar.symbol
WHERE bar.provider = ANY(%s)
  AND bar.symbol = ANY(%s)
  AND bar.bar_at >= %s
ORDER BY bar.provider, bar.symbol, bar.bar_at
