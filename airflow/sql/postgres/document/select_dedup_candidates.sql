-- 중복 후보: 같은 출처에서 발행(없으면 발견) 시각 ±12시간 안의 이웃 문서.
-- 창이 12시간인 이유: 속보→본기사 간격(분~시간)은 덮고, `[표] 오늘의 환율` 같은
-- 매일 반복 기사(24시간 간격)는 오판하지 않는다.
-- canonical_document_id를 함께 내려 이미 연결된 문서의 root를 따라갈 수 있게 한다.
SELECT
    id,
    title,
    published_at,
    detected_at,
    coalesce(length(summary), 0) + coalesce(length(body), 0) AS content_length,
    canonical_document_id
FROM document
WHERE source_slug = %s
  AND id <> %s
  AND coalesce(published_at, detected_at)
      BETWEEN %s::timestamptz - interval '12 hours' AND %s::timestamptz + interval '12 hours'
