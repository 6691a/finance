-- 환율 수집이 살아 있는지 보는 유일한 방법.
-- exchange_rate는 외부 DB에서 형태를 그대로 가져온 테이블이라 source_record_id가 없다.
-- 그래서 계보가 아니라 신선도로 판단한다. created_at은 우리가 넣은 시각이고 date는 고시일이다.
SELECT max(date)       AS latest_date,
       max(created_at) AS last_inserted_at
FROM exchange_rate
