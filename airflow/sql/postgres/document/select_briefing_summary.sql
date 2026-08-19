-- 브리핑 창의 문서 집계 한 행.
-- 창 하나를 CTE로 묶어 파라미터를 한 번만 받는다. FILTER 절마다 같은 값을 다시 넘기면
-- 순서가 어긋날 때 조용히 다른 구간을 세게 된다.
-- backlog는 평가를 스킵하는 중복(canonical_document_id)을 빼고 센다. 안 빼면 영원히 남는다.
-- backlog는 창 밖까지 본다. "아직 평가하지 못한 문서"에 시간 제한을 두면 오래 밀린 것이
-- 집계에서 사라진다.
WITH bounds AS (
    SELECT %s::timestamptz AS since
)
SELECT count(*) FILTER (WHERE document.detected_at >= bounds.since)                    AS detected,
       count(*) FILTER (WHERE document.assessed_at >= bounds.since)                    AS assessed,
       count(*) FILTER (WHERE document.assessed_at >= bounds.since
                          AND document.direction = 'positive')                         AS positive,
       count(*) FILTER (WHERE document.assessed_at >= bounds.since
                          AND document.direction = 'negative')                         AS negative,
       count(*) FILTER (WHERE document.assessed_at >= bounds.since
                          AND document.direction = 'neutral')                          AS neutral,
       count(*) FILTER (WHERE document.assessed_at IS NULL
                          AND document.canonical_document_id IS NULL)         AS backlog,
       min(document.detected_at) FILTER (WHERE document.assessed_at IS NULL
                                    AND document.canonical_document_id IS NULL) AS oldest_pending
FROM document
CROSS JOIN bounds
