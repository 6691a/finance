-- 방금 upsert한 거래일이 실제로 남았는지 센다.
--
-- **원장(`source_record.record_count`)은 응답 행 수다.** 그것을 저장 결과로 그대로 돌려주면
-- 한 행도 안 들어간 실행이 "30행 저장"으로 남고, 표와 차트는 조용히 옛날 값을 계속 그린다
-- (2026-08-20 실측: 원장은 30행·07-08~08-20인데 테이블에는 08-20 한 행뿐이었다).
-- 그래서 이 응답이 만든 source_record_id로 다시 세어 대조한다.
SELECT count(*)
FROM stock_investor_trade_daily
WHERE provider = 'kis'
  AND stock_code = %s
  AND business_date = ANY(%s)
  AND source_record_id = %s
