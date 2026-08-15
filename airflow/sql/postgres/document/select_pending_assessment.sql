-- 아직 평가하지 않았거나 다시 평가해야 하는 문서.
--
-- 세 가지가 대상이다. 한 번도 평가하지 않은 문서, 본문이 바뀐 문서, 프롬프트 버전이 오른
-- 문서다. `assessed_content_hash`가 없으면 같은 문서를 매번 다시 평가하거나 영영 안 하거나
-- 둘 중 하나가 된다.
--
-- 최근 것부터 집는다. 밀린 과거보다 방금 들어온 문서가 리포트에 먼저 필요하다.
SELECT
    id,
    source_slug,
    title,
    summary,
    body,
    language,
    published_at,
    content_hash
FROM document
WHERE assessed_at IS NULL
   OR assessed_content_hash IS DISTINCT FROM content_hash
   OR prompt_version IS DISTINCT FROM %s
ORDER BY published_at DESC NULLS LAST, id DESC
LIMIT %s
