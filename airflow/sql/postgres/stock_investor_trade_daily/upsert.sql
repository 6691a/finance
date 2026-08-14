-- 종목별 투자자 매매동향 확정 일별값 upsert.
--
-- 멱등 키는 (provider, stock_code, business_date)다. 한 응답이 30 거래일을 담고 백필이 날짜를
-- 뒤로 걸으므로 같은 거래일을 여러 번 받는다. 확정값이라 다시 받아도 값이 같다.
--
-- **단위가 컬럼마다 다르다.** 수량은 주, 투자자별 대금은 백만원, accumulated_trade_amount만
-- 원이다. 여기서는 KIS 표기를 그대로 넣고 환산하지 않는다.
INSERT INTO stock_investor_trade_daily (
    provider,
    stock_code,
    business_date,
    open_price,
    high_price,
    low_price,
    close_price,
    accumulated_volume,
    accumulated_trade_amount,
    foreign_net_buy_qty,
    foreign_registered_net_buy_qty,
    foreign_unregistered_net_buy_qty,
    individual_net_buy_qty,
    institution_net_buy_qty,
    securities_net_buy_qty,
    investment_trust_net_buy_qty,
    private_equity_net_buy_qty,
    bank_net_buy_qty,
    insurance_net_buy_qty,
    merchant_bank_net_buy_qty,
    pension_fund_net_buy_qty,
    other_corporation_net_buy_qty,
    other_organization_net_buy_qty,
    foreign_net_buy_amount,
    institution_net_buy_amount,
    individual_net_buy_amount,
    source_record_id
) VALUES ('kis', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT uq_stock_investor_trade_daily_natural_key DO UPDATE SET
    open_price = EXCLUDED.open_price,
    high_price = EXCLUDED.high_price,
    low_price = EXCLUDED.low_price,
    close_price = EXCLUDED.close_price,
    accumulated_volume = EXCLUDED.accumulated_volume,
    accumulated_trade_amount = EXCLUDED.accumulated_trade_amount,
    foreign_net_buy_qty = EXCLUDED.foreign_net_buy_qty,
    foreign_registered_net_buy_qty = EXCLUDED.foreign_registered_net_buy_qty,
    foreign_unregistered_net_buy_qty = EXCLUDED.foreign_unregistered_net_buy_qty,
    individual_net_buy_qty = EXCLUDED.individual_net_buy_qty,
    institution_net_buy_qty = EXCLUDED.institution_net_buy_qty,
    securities_net_buy_qty = EXCLUDED.securities_net_buy_qty,
    investment_trust_net_buy_qty = EXCLUDED.investment_trust_net_buy_qty,
    private_equity_net_buy_qty = EXCLUDED.private_equity_net_buy_qty,
    bank_net_buy_qty = EXCLUDED.bank_net_buy_qty,
    insurance_net_buy_qty = EXCLUDED.insurance_net_buy_qty,
    merchant_bank_net_buy_qty = EXCLUDED.merchant_bank_net_buy_qty,
    pension_fund_net_buy_qty = EXCLUDED.pension_fund_net_buy_qty,
    other_corporation_net_buy_qty = EXCLUDED.other_corporation_net_buy_qty,
    other_organization_net_buy_qty = EXCLUDED.other_organization_net_buy_qty,
    foreign_net_buy_amount = EXCLUDED.foreign_net_buy_amount,
    institution_net_buy_amount = EXCLUDED.institution_net_buy_amount,
    individual_net_buy_amount = EXCLUDED.individual_net_buy_amount,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
