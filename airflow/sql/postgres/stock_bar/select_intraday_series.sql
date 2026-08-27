-- 장중 차트가 국내 종목의 당일 분봉 종가를 읽는다. 일반 발송은 KRX·NXT를, 15:35 KRX
-- 마감 발송은 KRX만 넘긴다. 같은 분에 둘 다 있으면 KRX를 우선한다. quote_bar 뷰의
-- select_intraday_series.sql보다 허용 거래소와 결과의 exchange 열이 하나씩 더 있다.
SELECT DISTINCT ON (bar.provider, bar.stock_code, bar.bar_at)
       bar.provider,
       bar.stock_code,
       symbol.label,
       bar.bar_at,
       bar.close,
       bar.exchange
FROM stock_bar AS bar
JOIN quote_symbol AS symbol
  ON symbol.provider = bar.provider
 AND symbol.symbol = bar.stock_code
WHERE bar.provider = ANY(%s)
  AND bar.stock_code = ANY(%s)
  AND bar.bar_at >= %s
  AND bar.exchange = ANY(%s)
ORDER BY bar.provider, bar.stock_code, bar.bar_at, (bar.exchange = 'KRX') DESC
