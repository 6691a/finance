-- 코스피·코스닥의 상승·보합·하락 종목 수 한 분을 저장한다. 테이블은 백엔드 마이그레이션이 만든다.
-- 멱등 키는 (provider, symbol, observed_at)다.
--
-- **다섯 값이 모두 0인 응답은 여기까지 오지 않는다.** 장 밖 리셋 상태라 분포가 아니고,
-- 그 판정은 수집기가 한다.
--
-- 같은 분에 REST와 WebSocket 값이 겹치면 마지막 정상값이 남고 source_record_id가 그 행을
-- 실제로 갱신한 원천을 가리킨다.
--
-- 정의의 원본은 `apps/models/market.py`의 `MarketMovementSnapshot`이고
-- `tests/collectors/test_kis.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO market_movement_snapshot (
    provider,
    symbol,
    observed_at,
    upper_limit_count,
    rising_count,
    unchanged_count,
    falling_count,
    lower_limit_count,
    source_record_id
) VALUES ('kis', %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, symbol, observed_at) DO UPDATE SET
    upper_limit_count = EXCLUDED.upper_limit_count,
    rising_count = EXCLUDED.rising_count,
    unchanged_count = EXCLUDED.unchanged_count,
    falling_count = EXCLUDED.falling_count,
    lower_limit_count = EXCLUDED.lower_limit_count,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
