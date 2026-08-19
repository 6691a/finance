-- 장중 차트가 국내 종목의 당일 분봉 종가를 읽는다. 분마다 KRX·NXT 중 하나를 고르며
-- 같은 분에 둘 다 있으면 KRX를 우선한다. 그래서 정규장은 KRX, 15:30 이후는 NXT 봉이
-- 한 줄로 이어진다. quote_bar 뷰의 select_intraday_series.sql과 바인드 모양이 같다.
SELECT DISTINCT ON (bar.provider, bar.stock_code, bar.bar_at)
       bar.provider,
       bar.stock_code,
       symbol.label,
       bar.bar_at,
       bar.close
FROM stock_bar AS bar
JOIN quote_symbol AS symbol
  ON symbol.provider = bar.provider
 AND symbol.symbol = bar.stock_code
WHERE bar.provider = ANY(%s)
  AND bar.stock_code = ANY(%s)
  AND bar.bar_at >= %s
  AND bar.exchange IN ('KRX', 'NXT')
ORDER BY bar.provider, bar.stock_code, bar.bar_at, (bar.exchange = 'KRX') DESC
