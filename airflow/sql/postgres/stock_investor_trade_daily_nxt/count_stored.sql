-- 방금 upsert한 NXT 거래일이 실제로 남았는지 센다. KRX판(`../stock_investor_trade_daily/count_stored.sql`)과
-- 같은 이유다: 원장(`source_record.record_count`)은 응답 행 수라서 저장 결과로 그대로 돌려주면 한 행도
-- 안 들어간 실행이 "30행 저장"으로 남는다. 이 응답이 만든 source_record_id로 다시 세어 대조한다.
SELECT count(*)
FROM stock_investor_trade_daily_nxt
WHERE provider = 'kis'
  AND stock_code = %s
  AND business_date = ANY(%s)
  AND source_record_id = %s
