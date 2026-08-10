-- 지수·선물의 1분봉을 분 단위로 누적한다. 테이블은 백엔드 마이그레이션이 만든다.
-- 멱등 키는 (provider, symbol, bar_at)다. 폴링 주기보다 넓은 구간을 받아도 겹치는 봉은
-- 행을 늘리지 않고 최신 값으로 갱신한다. symbol은 제공처 안에서만 고유하므로
-- provider가 키에 함께 들어간다.
--
-- 정의의 원본은 `apps/models/market.py`의 `QuoteBar`이고
-- `tests/collectors/test_yahoo.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO quote_bar (
    provider,
    symbol,
    bar_at,
    open,
    high,
    low,
    close,
    volume,
    previous_close,
    contract_code,
    source_record_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, symbol, bar_at) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    previous_close = EXCLUDED.previous_close,
    contract_code = EXCLUDED.contract_code,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
