-- Slack 브리핑이 심볼마다 마지막 봉 하나를 읽는다.
-- 등락은 봉에 이미 있는 previous_close로 계산하므로 전일 세션을 따로 찾지 않는다.
-- 나라·종류로 거르지 않고 전부 받아 파이썬이 리포트별로 나눈다. 심볼 수가 수십 개라
-- 쿼리를 리포트마다 나누는 값어치가 없다.
SELECT DISTINCT ON (bar.provider, bar.symbol)
       bar.provider,
       bar.symbol,
       symbol.label,
       symbol.kind,
       symbol.country,
       bar.close,
       bar.previous_close,
       bar.bar_at
FROM quote_bar AS bar
JOIN quote_symbol AS symbol
  ON symbol.provider = bar.provider
 AND symbol.symbol = bar.symbol
WHERE bar.bar_at >= %s
ORDER BY bar.provider, bar.symbol, bar.bar_at DESC
