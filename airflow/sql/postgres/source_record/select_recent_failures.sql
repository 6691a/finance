-- 창 안의 최근 실패 몇 건. 원인은 metadata에 수집기가 남긴 것을 잘라서 보여 준다.
-- 모델 속성은 source_metadata지만 컬럼 이름은 metadata다.
SELECT source,
       source_key,
       started_at,
       left(metadata::text, 300) AS detail
FROM source_record
WHERE started_at >= %s
  AND status = 'failed'
ORDER BY started_at DESC
LIMIT %s
