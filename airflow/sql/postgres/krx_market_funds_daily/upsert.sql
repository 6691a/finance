-- 증시자금 종합 하루치를 저장한다. 멱등 키는 (provider, business_date)다.
--
-- 한 응답에 100영업일이 오므로 이 문장은 한 번의 조회에서 100번 실행된다. 자연키 upsert라
-- 겹치는 날짜는 최신 값으로 갱신되고 정정도 그대로 흡수된다.
--
-- **prdy_ctrt는 저장하지 않는다.** 실측 값이 등락률과 맞지 않았다(모델 주석 참고).
INSERT INTO krx_market_funds_daily (
    provider, business_date, index_close, index_change, market_capitalization,
    customer_deposit, customer_deposit_change, turnover_ratio, unsettled_amount,
    credit_loan_balance, futures_margin_amount,
    equity_fund_amount, mixed_fund_amount, bond_fund_amount, mmf_amount,
    securities_lending_amount,
    source_record_id
) VALUES ('kis', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, business_date) DO UPDATE SET
    index_close = EXCLUDED.index_close,
    index_change = EXCLUDED.index_change,
    market_capitalization = EXCLUDED.market_capitalization,
    customer_deposit = EXCLUDED.customer_deposit,
    customer_deposit_change = EXCLUDED.customer_deposit_change,
    turnover_ratio = EXCLUDED.turnover_ratio,
    unsettled_amount = EXCLUDED.unsettled_amount,
    credit_loan_balance = EXCLUDED.credit_loan_balance,
    futures_margin_amount = EXCLUDED.futures_margin_amount,
    equity_fund_amount = EXCLUDED.equity_fund_amount,
    mixed_fund_amount = EXCLUDED.mixed_fund_amount,
    bond_fund_amount = EXCLUDED.bond_fund_amount,
    mmf_amount = EXCLUDED.mmf_amount,
    securities_lending_amount = EXCLUDED.securities_lending_amount,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
