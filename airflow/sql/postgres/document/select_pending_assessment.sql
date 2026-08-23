-- 아직 평가하지 않았거나 다시 평가해야 하는 문서.
--
-- 세 가지가 대상이다. 한 번도 평가하지 않은 문서, 본문이 바뀐 문서, 프롬프트 버전이 오른
-- 문서다. `assessed_content_hash`가 없으면 같은 문서를 매번 다시 평가하거나 영영 안 하거나
-- 둘 중 하나가 된다.
--
-- 대표에 연결된 중복(canonical_document_id)은 평가하지 않는다. 대표가 평가를 받는다.
--
-- 신규와 재평가를 따로 줄 세워 번갈아 집는다. 신규는 최근 것부터 — 밀린 과거보다 방금
-- 들어온 문서가 리포트에 먼저 필요하다. 재평가는 오래 방치된 것부터. 최신순 하나로 줄을
-- 세우면 신규 유입이 batch_size에 가까울 때 프롬프트 버전이 오른 과거 문서가 영영 차례를
-- 못 받는다. 한쪽이 비면 다른 쪽이 배치를 다 쓴다.
--
-- ponytail: 비율 1:1 고정. 재평가 백로그가 너무 느리게 빠지면 rank에 가중치를 준다.
WITH pending AS (
    SELECT
        id,
        source_slug,
        title,
        summary,
        body,
        language,
        published_at,
        content_hash,
        assessed_at IS NULL AS is_new,
        row_number() OVER (
            PARTITION BY assessed_at IS NULL
            ORDER BY
                CASE WHEN assessed_at IS NULL THEN published_at END DESC NULLS LAST,
                assessed_at ASC,
                id DESC
        ) AS rank
    FROM document
    WHERE canonical_document_id IS NULL
      AND (assessed_at IS NULL
           OR assessed_content_hash IS DISTINCT FROM content_hash
           OR prompt_version IS DISTINCT FROM %s)
)
SELECT
    id,
    source_slug,
    title,
    summary,
    body,
    language,
    published_at,
    content_hash
FROM pending
ORDER BY rank, is_new DESC, id DESC
LIMIT %s
