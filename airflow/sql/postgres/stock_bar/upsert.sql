-- 개별 종목의 1분봉을 거래소 단위로 누적한다. 테이블은 백엔드 마이그레이션이 만든다.
-- 멱등 키는 (provider, stock_code, exchange, bar_at)다. 같은 종목이 KRX와 NXT에서 따로
-- 체결되므로 거래소가 키에 들어간다. 통합(UN) 시세는 받지 않는다.
--
-- 정의의 원본은 `apps/models/market.py`의 `StockBar`이고
-- `tests/collectors/test_kis.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO stock_bar (
    provider,
    stock_code,
    exchange,
    bar_at,
    open,
    high,
    low,
    close,
    volume,
    previous_close,
    source_record_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, stock_code, exchange, bar_at) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    previous_close = EXCLUDED.previous_close,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
