-- 종목별 대차거래 하루치를 저장한다. 멱등 키는 (provider, stock_code, business_date)다.
--
-- **이 값은 MRKT_DIV_CLS_CODE=3(종목 조회) 응답에서만 온다.** 1은 시장 전체라 종목 행에
-- 코스피 전체 숫자가 들어간다(실측).
INSERT INTO krx_stock_securities_lending_daily (
    provider, stock_code, business_date, close_price, price_change, accumulated_volume,
    new_quantity, repayment_quantity, balance_change_quantity,
    balance_quantity, balance_amount,
    source_record_id
) VALUES ('kis', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, stock_code, business_date) DO UPDATE SET
    close_price = EXCLUDED.close_price,
    price_change = EXCLUDED.price_change,
    accumulated_volume = EXCLUDED.accumulated_volume,
    new_quantity = EXCLUDED.new_quantity,
    repayment_quantity = EXCLUDED.repayment_quantity,
    balance_change_quantity = EXCLUDED.balance_change_quantity,
    balance_quantity = EXCLUDED.balance_quantity,
    balance_amount = EXCLUDED.balance_amount,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
