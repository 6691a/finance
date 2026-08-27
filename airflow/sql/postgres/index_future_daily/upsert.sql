-- 지수선물의 일봉을 거래일 단위로 누적한다. 테이블은 백엔드 마이그레이션이 만든다.
-- 멱등 키는 (provider, symbol, business_date)다. 장이 열려 있는 동안 받은 마지막 봉은
-- 미완성이라 다음 실행이 확정값으로 덮는다.
--
-- `contract_code`는 KIS 국내선물이 그날 실제로 조회한 월물이고 Yahoo 연속 심볼(ES=F)은 NULL이다.
-- 자연키에는 안 들어간다 — 최근월물은 날짜마다 하나뿐이고, 키에 넣으면 롤 하는 날
-- 같은 날짜에 두 행이 생긴다.
--
-- 정의의 원본은 `apps/models/market/series.py`의 `IndexFutureDaily`이고
-- 수집기 테스트가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO index_future_daily (
    provider,
    symbol,
    business_date,
    open,
    high,
    low,
    close,
    volume,
    contract_code,
    source_record_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, symbol, business_date) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    contract_code = EXCLUDED.contract_code,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
