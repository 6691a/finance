-- 신용잔고 상위 스냅샷의 순위 한 칸을 저장한다.
-- 멱등 키는 (provider, standard_date, universe_code, sort_code, period_days, rank)다.
--
-- **standard_date는 응답의 stnd_date2다.** 둘 중 최신 날짜이고 stnd_date1이 비교일이다.
-- 같은 기준일을 다시 받으면 순위 슬롯별로 갱신되며, 응답이 짧아졌을 때 남는 슬롯은
-- delete_stale_ranks.sql이 지운다.
INSERT INTO krx_credit_balance_ranking_daily (
    provider, standard_date, comparison_date, universe_code, sort_code, period_days, rank,
    stock_code, stock_name, close_price, accumulated_volume,
    loan_balance_quantity, loan_balance_amount, loan_balance_rate,
    short_loan_balance_quantity, short_loan_balance_amount, short_loan_balance_rate,
    loan_balance_growth_rate, short_loan_balance_growth_rate,
    source_record_id
) VALUES ('kis', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, standard_date, universe_code, sort_code, period_days, rank) DO UPDATE SET
    comparison_date = EXCLUDED.comparison_date,
    stock_code = EXCLUDED.stock_code,
    stock_name = EXCLUDED.stock_name,
    close_price = EXCLUDED.close_price,
    accumulated_volume = EXCLUDED.accumulated_volume,
    loan_balance_quantity = EXCLUDED.loan_balance_quantity,
    loan_balance_amount = EXCLUDED.loan_balance_amount,
    loan_balance_rate = EXCLUDED.loan_balance_rate,
    short_loan_balance_quantity = EXCLUDED.short_loan_balance_quantity,
    short_loan_balance_amount = EXCLUDED.short_loan_balance_amount,
    short_loan_balance_rate = EXCLUDED.short_loan_balance_rate,
    loan_balance_growth_rate = EXCLUDED.loan_balance_growth_rate,
    short_loan_balance_growth_rate = EXCLUDED.short_loan_balance_growth_rate,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
