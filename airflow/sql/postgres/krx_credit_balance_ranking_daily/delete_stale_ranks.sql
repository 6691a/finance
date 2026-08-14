-- 같은 기준일의 응답이 이전보다 짧아졌을 때 남는 순위 슬롯을 지운다.
--
-- 이게 없으면 순위에서 탈락한 종목이 마지막 순위 밖에 유령 행으로 남는다. 응답 건수를
-- 상수로 박지 않으므로 "이번에 받은 마지막 순위"를 넘는 것만 지운다.
DELETE FROM krx_credit_balance_ranking_daily
WHERE provider = 'kis'
  AND standard_date = %s
  AND universe_code = %s
  AND sort_code = %s
  AND period_days = %s
  AND rank > %s
