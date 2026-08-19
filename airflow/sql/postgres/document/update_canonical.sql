-- 문서 하나를 대표 문서에 연결한다. 판정은 modules/dedup.py가 한다.
-- 오판이면 이 컬럼을 NULL로 되돌리면 끝이다. 문서를 지우지 않는다.
UPDATE document
SET canonical_document_id = %s,
    updated_at = now()
WHERE id = %s
