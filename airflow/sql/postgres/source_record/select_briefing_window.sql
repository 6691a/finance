-- 창 안의 수집 실행을 제공처별로 접는다. (source, started_at) 인덱스를 탄다.
-- record_count가 0인 실행도 센다. 조회했지만 값이 없는 구간과 아직 조회하지 않은 구간을
-- 가르는 것이 source_record의 존재 이유다.
SELECT source,
       count(*)                                       AS runs,
       count(*) FILTER (WHERE status = 'succeeded')   AS succeeded,
       count(*) FILTER (WHERE status = 'failed')      AS failed,
       coalesce(sum(record_count), 0)                 AS records,
       max(completed_at)                              AS last_completed_at
FROM source_record
WHERE started_at >= %s
GROUP BY source
ORDER BY source
