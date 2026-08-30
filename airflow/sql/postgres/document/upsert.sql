-- 발견한 문서 하나를 저장한다. 테이블은 백엔드 마이그레이션이 만든다.
-- 멱등 키는 (source_slug, external_id)다. content_hash를 키에 넣지 않는다. 넣으면 본문이
-- 조금만 달라져도 새 행이 생겨 같은 기사가 매시간 쌓인다.
--
-- detected_at은 처음 본 시각이라 갱신하지 않는다. 갱신하면 "언제부터 있었나"가 사라진다.
-- canonical_document_id도 건드리지 않는다. 중복 판정은 별도 단계가 쓴다.
--
-- 정의의 원본은 `apps/models/content.py`의 `Document`이고
-- `tests/collectors/test_documents.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO document (
    source_slug,
    external_id,
    canonical_url,
    document_type,
    title,
    summary,
    body,
    language,
    published_at,
    detected_at,
    content_hash,
    source_record_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (source_slug, external_id) DO UPDATE SET
    canonical_url = EXCLUDED.canonical_url,
    document_type = EXCLUDED.document_type,
    title = EXCLUDED.title,
    summary = EXCLUDED.summary,
    body = EXCLUDED.body,
    language = EXCLUDED.language,
    published_at = EXCLUDED.published_at,
    content_hash = EXCLUDED.content_hash,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
WHERE document.content_hash IS DISTINCT FROM EXCLUDED.content_hash
