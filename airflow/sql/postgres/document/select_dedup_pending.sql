-- 중복 판정 대상: 아직 평가하지 않았고 대표에 연결되지도 않은 문서.
-- 평가(select_pending_assessment.sql)가 붙기 전에 판정을 끝내야 하므로 같은 조건보다 좁다.
-- content_length는 대표 선정 기준이다. 본문이 긴 쪽이 대표가 된다(modules/dedup.py).
-- content_hash는 설계 §6.4 ②의 규칙 판정에 쓴다. 값이 같으면 제목·요약·본문이 글자 그대로 같다.
SELECT
    id,
    source_slug,
    title,
    published_at,
    detected_at,
    coalesce(length(summary), 0) + coalesce(length(body), 0) AS content_length,
    content_hash
FROM document
WHERE assessed_at IS NULL
  AND canonical_document_id IS NULL
ORDER BY published_at DESC NULLS LAST, id DESC
LIMIT %s
