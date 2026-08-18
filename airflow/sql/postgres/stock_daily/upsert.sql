-- 개별 종목의 일봉을 거래소 단위로 누적한다. 테이블은 백엔드 마이그레이션이 만든다.
-- 해외 상장 종목(TSMC ADR)용이다. 국내 종목 일봉은 stock_investor_trade_daily가 갖는다.
--
-- 정의의 원본은 `apps/models/market.py`의 `StockDaily`이고
-- `tests/collectors/test_yahoo.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO stock_daily (
    provider,
    stock_code,
    exchange,
    business_date,
    open,
    high,
    low,
    close,
    volume,
    source_record_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, stock_code, exchange, business_date) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
