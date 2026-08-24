-- 이벤트 주장 추출을 기다리는 문서.
--
-- 평가가 끝났고(assessed_at) 종목 태그가 붙은 문서 중, 아직 안 뽑았거나 본문이 바뀌었거나
-- 프롬프트 판이 오른 것이다. 종목 태그를 조건으로 쓰는 이유: 종목 이벤트 주장은 종목이
-- 언급된 문서에만 있고, 태그 없는 문서(시황·채권)까지 LLM에 넣으면 비용만 는다.
-- 태그는 평가가 만들므로 평가 완료가 선행 조건이다.
--
-- 대표에 연결된 중복(canonical_document_id)은 뽑지 않는다 — 대표가 뽑힌다.
-- tickers는 그 문서의 태그 목록이다. 추출 주장의 stock_code를 이 목록으로 좁힌다.
SELECT
    d.id,
    d.source_slug,
    d.title,
    d.summary,
    d.body,
    d.published_at,
    d.detected_at,
    d.content_hash,
    (
        SELECT array_agg(di.ticker ORDER BY di.ticker)
        FROM document_instrument di
        WHERE di.document_id = d.id
    ) AS tickers
FROM document d
WHERE d.canonical_document_id IS NULL
  AND d.assessed_at IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM document_instrument di
      WHERE di.document_id = d.id
  )
  AND NOT EXISTS (
      SELECT 1
      FROM stock_event_extraction e
      WHERE e.document_id = d.id
        AND e.extracted_content_hash = d.content_hash
        AND e.prompt_version = %s
  )
ORDER BY d.published_at DESC NULLS LAST, d.id DESC
LIMIT %s
