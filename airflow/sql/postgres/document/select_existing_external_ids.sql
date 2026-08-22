-- 목록 수집이 상세 페이지를 받기 전에 이미 있는 항목을 가른다.
--
-- 목록만으로 만든 항목을 다시 upsert하면 `content_hash`가 달라져(summary가 NULL)
-- 상세 요약이 지워지고 재평가가 돈다. 그래서 새 항목만 상세를 받고 기존 항목은 이번
-- 실행에서 아예 뺀다. 자연키는 (source_slug, external_id)다.
SELECT external_id
FROM document
WHERE source_slug = %s
  AND external_id = ANY(%s)
