-- 중복 판정 대상: 아직 평가하지 않았고 대표에 연결되지도 않은 문서.
-- 평가(select_pending_assessment.sql)가 붙기 전에 판정을 끝내야 하므로 같은 조건보다 좁다.
-- content_length는 대표 선정 기준이다. 본문이 긴 쪽이 대표가 된다(modules/dedup.py).
SELECT
    id,
    source_slug,
    title,
    published_at,
    detected_at,
    coalesce(length(summary), 0) + coalesce(length(body), 0) AS content_length
FROM document
WHERE assessed_at IS NULL
  AND canonical_document_id IS NULL
ORDER BY published_at DESC NULLS LAST, id DESC
LIMIT %s
