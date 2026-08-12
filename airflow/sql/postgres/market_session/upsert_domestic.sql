-- 국내(KRX) 거래일 한 날을 저장한다. 테이블은 백엔드 마이그레이션이 만든다.
-- 멱등 키는 (market_code, session_date)다.
--
-- 국내는 KIS 국내휴장일조회가 판정의 주인이므로 effective_open_day와 verified_by를 함께 쓴다.
-- 정의의 원본은 `apps/models/market.py`의 `MarketSession`이고
-- `tests/collectors/test_kis_market_calendar.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO market_session (
    market_code,
    market_name,
    country_code,
    session_date,
    kis_weekday_code,
    kis_business_day,
    kis_trading_day,
    kis_open_day,
    kis_settlement_day,
    effective_open_day,
    verified_by,
    verified_at,
    source_record_id
) VALUES ('KRX', '한국거래소', 'KR', %s, %s, %s, %s, %s, %s, %s, 'kis', %s, %s)
ON CONFLICT (market_code, session_date) DO UPDATE SET
    kis_weekday_code = EXCLUDED.kis_weekday_code,
    kis_business_day = EXCLUDED.kis_business_day,
    kis_trading_day = EXCLUDED.kis_trading_day,
    kis_open_day = EXCLUDED.kis_open_day,
    kis_settlement_day = EXCLUDED.kis_settlement_day,
    effective_open_day = EXCLUDED.effective_open_day,
    verified_by = EXCLUDED.verified_by,
    verified_at = EXCLUDED.verified_at,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
