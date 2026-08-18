-- 암호화폐의 일봉을 거래일 단위로 누적한다. 테이블은 백엔드 마이그레이션이 만든다.
-- 멱등 키는 (provider, symbol, business_date)다. 장이 열려 있는 동안 받은 마지막 봉은
-- 미완성이라 다음 실행이 확정값으로 덮는다.
--
-- 정의의 원본은 `apps/models/market.py`의 `CryptoDaily`이고
-- 수집기 테스트가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO crypto_daily (
    provider,
    symbol,
    business_date,
    open,
    high,
    low,
    close,
    volume,
    source_record_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, symbol, business_date) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
