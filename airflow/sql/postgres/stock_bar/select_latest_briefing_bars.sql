-- Slack 브리핑이 국내 종목의 마지막 봉 하나를 읽는다. quote_bar 뷰는 NXT를 태우지
-- 않으므로(거래소가 섞임) 물리 테이블을 직접 본다.
-- KRX·NXT 중 최신 봉을 고르되 같은 분이면 KRX를 우선한다. 정규장(09:00~15:30)은 KRX가,
-- 이후(NXT 애프터마켓 ~20:00)는 NXT가 자연히 이긴다. NXT 봉의 previous_close도 KRX 확정
-- 종가라(스키마 주석) 등락률 분모가 거래소와 무관하게 같다.
SELECT DISTINCT ON (bar.provider, bar.stock_code)
       bar.provider,
       bar.stock_code,
       symbol.label,
       symbol.kind,
       symbol.country,
       bar.close,
       bar.previous_close,
       bar.bar_at,
       bar.exchange
FROM stock_bar AS bar
JOIN quote_symbol AS symbol
  ON symbol.provider = bar.provider
 AND symbol.symbol = bar.stock_code
WHERE bar.bar_at >= %s
  AND bar.exchange IN ('KRX', 'NXT')
ORDER BY bar.provider, bar.stock_code, bar.bar_at DESC, (bar.exchange = 'KRX') DESC
