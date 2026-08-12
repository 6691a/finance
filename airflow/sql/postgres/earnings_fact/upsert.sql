-- 공시에서 추출한 실적 지표 한 칸을 저장한다.
-- 멱등 키는 (provider, rcept_no, statement_scope, amount_basis, metric)다.
--
-- **새 접수번호의 정정 공시는 새 행이다.** 여기서 덮이는 것은 같은 접수번호의 첨부가
-- 바뀐 경우뿐이고, 그 변화는 source_record의 sha256으로 식별한다.
INSERT INTO earnings_fact (
    provider,
    stock_code,
    rcept_no,
    release_type,
    period_end,
    statement_scope,
    amount_basis,
    metric,
    current_amount,
    prior_year_amount,
    currency,
    source_account_id,
    source_account_name,
    source_record_id
) VALUES ('dart', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, rcept_no, statement_scope, amount_basis, metric) DO UPDATE SET
    release_type = EXCLUDED.release_type,
    period_end = EXCLUDED.period_end,
    current_amount = EXCLUDED.current_amount,
    prior_year_amount = EXCLUDED.prior_year_amount,
    currency = EXCLUDED.currency,
    source_account_id = EXCLUDED.source_account_id,
    source_account_name = EXCLUDED.source_account_name,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
