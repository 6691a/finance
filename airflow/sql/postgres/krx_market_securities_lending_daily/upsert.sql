-- 코스피·코스닥 시장 전체의 대차거래 하루치를 저장한다.
-- 멱등 키는 (provider, market_code, business_date)다.
--
-- 종목 대차와 같은 endpoint의 조회 분류만 다르다. `1`이 코스피, `2`가 코스닥이다(실측).
-- **합계(`5`)는 저장하지 않는다.** 5영업일 내내 코스피와 코스닥의 정확한 합이었다.
INSERT INTO krx_market_securities_lending_daily (
    provider, market_code, business_date, index_close, index_change, accumulated_volume,
    new_quantity, repayment_quantity, balance_change_quantity,
    balance_quantity, balance_amount,
    source_record_id
) VALUES ('kis', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, market_code, business_date) DO UPDATE SET
    index_close = EXCLUDED.index_close,
    index_change = EXCLUDED.index_change,
    accumulated_volume = EXCLUDED.accumulated_volume,
    new_quantity = EXCLUDED.new_quantity,
    repayment_quantity = EXCLUDED.repayment_quantity,
    balance_change_quantity = EXCLUDED.balance_change_quantity,
    balance_quantity = EXCLUDED.balance_quantity,
    balance_amount = EXCLUDED.balance_amount,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
