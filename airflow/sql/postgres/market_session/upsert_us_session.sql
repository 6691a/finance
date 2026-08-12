-- 미국 현물시장(US_EQUITY)의 하루를 NYSE 판정으로 저장한다.
--
-- **결제일 두 컬럼과 verification_source_record_id를 건드리지 않는다.** 그 값은 KIS
-- 해외결제일자조회가 채우며, 매일 도는 NYSE 태스크가 덮어쓰면 안 된다.
--
-- 미국을 거래소별로 나누지 않는 이유는 `docs/kis-market-session-calendar.md` §5에 있다.
INSERT INTO market_session (
    market_code,
    market_name,
    country_code,
    session_date,
    effective_open_day,
    verified_by,
    verified_at,
    source_record_id
) VALUES ('US_EQUITY', '미국 현물시장', 'US', %s, %s, 'nyse', %s, %s)
ON CONFLICT (market_code, session_date) DO UPDATE SET
    effective_open_day = EXCLUDED.effective_open_day,
    verified_by = EXCLUDED.verified_by,
    verified_at = EXCLUDED.verified_at,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
