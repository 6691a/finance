-- 지표 관측값을 날짜 단위로 누적한다. 테이블은 백엔드 마이그레이션이 만든다.
-- 멱등 키는 (series_id, observation_date)다. 같은 날짜를 다시 수집하면 행을 늘리지 않고
-- 최신 발표 값과 그 근거 레코드로 갱신한다.
INSERT INTO indicator_observation (
    series_id,
    observation_date,
    value,
    unit,
    source_record_id
) VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (series_id, observation_date) DO UPDATE SET
    value = EXCLUDED.value,
    unit = EXCLUDED.unit,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
