-- 종목별 신용잔고 하루치를 저장한다. 테이블은 백엔드 마이그레이션이 만든다.
-- 멱등 키는 (provider, stock_code, trade_date)다.
--
-- **거래일이 키이고 결제일은 값이다.** 이 API의 입력은 결제일이지만 사용자가 보는 추이는
-- 거래일 기준이다. 실측에서 결제 시차가 2영업일이었다.
--
-- 정의의 원본은 `apps/models/market.py`의 `KrxStockCreditBalanceDaily`이고
-- `tests/collectors/test_kis.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO krx_stock_credit_balance_daily (
    provider, stock_code, trade_date, settlement_date, close_price, accumulated_volume,
    loan_new_quantity, loan_repayment_quantity, loan_balance_quantity,
    loan_new_amount, loan_repayment_amount, loan_balance_amount,
    loan_balance_rate, loan_supply_rate,
    short_loan_new_quantity, short_loan_repayment_quantity, short_loan_balance_quantity,
    short_loan_new_amount, short_loan_repayment_amount, short_loan_balance_amount,
    short_loan_balance_rate, short_loan_supply_rate,
    source_record_id
) VALUES ('kis', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, stock_code, trade_date) DO UPDATE SET
    settlement_date = EXCLUDED.settlement_date,
    close_price = EXCLUDED.close_price,
    accumulated_volume = EXCLUDED.accumulated_volume,
    loan_new_quantity = EXCLUDED.loan_new_quantity,
    loan_repayment_quantity = EXCLUDED.loan_repayment_quantity,
    loan_balance_quantity = EXCLUDED.loan_balance_quantity,
    loan_new_amount = EXCLUDED.loan_new_amount,
    loan_repayment_amount = EXCLUDED.loan_repayment_amount,
    loan_balance_amount = EXCLUDED.loan_balance_amount,
    loan_balance_rate = EXCLUDED.loan_balance_rate,
    loan_supply_rate = EXCLUDED.loan_supply_rate,
    short_loan_new_quantity = EXCLUDED.short_loan_new_quantity,
    short_loan_repayment_quantity = EXCLUDED.short_loan_repayment_quantity,
    short_loan_balance_quantity = EXCLUDED.short_loan_balance_quantity,
    short_loan_new_amount = EXCLUDED.short_loan_new_amount,
    short_loan_repayment_amount = EXCLUDED.short_loan_repayment_amount,
    short_loan_balance_amount = EXCLUDED.short_loan_balance_amount,
    short_loan_balance_rate = EXCLUDED.short_loan_balance_rate,
    short_loan_supply_rate = EXCLUDED.short_loan_supply_rate,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
