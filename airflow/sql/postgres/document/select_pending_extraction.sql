-- 이벤트 주장 추출을 기다리는 문서.
--
-- 평가가 끝났고(assessed_at) **추적 종목** 태그가 붙은 문서 중, 아직 안 뽑았거나 본문이
-- 바뀌었거나 프롬프트 판이 오른 것이다. 종목 태그를 조건으로 쓰는 이유: 종목 이벤트 주장은
-- 종목이 언급된 문서에만 있고, 태그 없는 문서(시황·채권)까지 LLM에 넣으면 비용만 는다.
-- 태그는 평가가 만들므로 평가 완료가 선행 조건이다.
--
-- **태그 후보보다 좁다.** 태그 후보는 `instrument` 전체(`select_taggable.sql`)인데 여기는
-- `is_watched`로 한 번 더 거른다. 주장의 실제값이 오는 `earnings_fact`를 DART 수집기가
-- 자기 Enum의 종목만 채우고, 판정 쪽 툴도 추적 종목 밖 코드를 거절한다. 후보와 같은 넓이로
-- 두면 아무도 읽지 않는 주장이 LLM 비용과 함께 쌓인다. 시세를 받는 종목이 늘면 여기도
-- 따라 넓어진다.
--
-- 대표에 연결된 중복(canonical_document_id)은 뽑지 않는다 — 대표가 뽑힌다.
-- tickers는 그 문서의 태그 중 **추적 종목만**이다. 추출 주장의 stock_code를 이 목록으로
-- 좁히므로, 여기에 판정할 수 없는 종목을 실으면 그 주장이 만들어졌다가 조용히 버려진다.
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
        JOIN instrument i ON i.ticker = di.ticker AND i.is_watched
        WHERE di.document_id = d.id
    ) AS tickers
FROM document d
WHERE d.canonical_document_id IS NULL
  AND d.assessed_at IS NOT NULL
  AND EXISTS (
      SELECT 1
      FROM document_instrument di
      JOIN instrument i ON i.ticker = di.ticker AND i.is_watched
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
