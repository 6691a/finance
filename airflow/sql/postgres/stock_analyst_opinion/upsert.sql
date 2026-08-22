-- 증권사 하나의 종목 투자의견 한 건을 저장한다. 멱등 키는
-- (provider, stock_code, business_date, broker_name)이다.
--
-- 같은 증권사가 같은 날 의견을 두 번 내지 않는다(2026-08-22 실측). 되돌아보기 구간을 매일
-- 다시 받으므로 같은 자연키가 다시 오면 값만 갱신한다.
INSERT INTO stock_analyst_opinion (
    provider, stock_code, business_date, broker_name,
    opinion, opinion_code, previous_opinion, previous_opinion_code,
    target_price, previous_close, gap_amount, gap_rate,
    source_record_id
) VALUES ('kis', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, stock_code, business_date, broker_name) DO UPDATE SET
    opinion = EXCLUDED.opinion,
    opinion_code = EXCLUDED.opinion_code,
    previous_opinion = EXCLUDED.previous_opinion,
    previous_opinion_code = EXCLUDED.previous_opinion_code,
    target_price = EXCLUDED.target_price,
    previous_close = EXCLUDED.previous_close,
    gap_amount = EXCLUDED.gap_amount,
    gap_rate = EXCLUDED.gap_rate,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
