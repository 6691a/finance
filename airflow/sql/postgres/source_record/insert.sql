-- 수집 1회를 계보 레코드 1행으로 남긴다.
-- 테이블은 백엔드 마이그레이션이 만든다. 그래서 이 폴더에는 create.sql이 없다.
-- id, created_at, updated_at은 DB 기본값이 채우므로 넘기지 않는다.
INSERT INTO source_record (
    source_type,
    source,
    source_key,
    started_at,
    completed_at,
    status,
    record_count,
    payload,
    metadata
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
RETURNING id
