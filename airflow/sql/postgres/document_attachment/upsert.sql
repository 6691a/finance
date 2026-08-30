-- 문서에 붙은 첨부 파일 또는 영상 링크 하나.
--
-- 멱등 키는 (document_id, url)이다. `position`을 키에 넣으면 페이지 마크업이 바뀔 때 순서가
-- 밀려 같은 파일이 새 행이 된다.
--
-- 정의의 원본은 `apps/models/content.py`의 `DocumentAttachment`이고
-- `tests/collectors/test_document_body.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO document_attachment (
    document_id,
    position,
    kind,
    url,
    storage_path,
    filename,
    media_type,
    byte_size,
    sha256,
    fetched_at
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (document_id, url) DO UPDATE SET
    position = EXCLUDED.position,
    kind = EXCLUDED.kind,
    storage_path = EXCLUDED.storage_path,
    filename = EXCLUDED.filename,
    media_type = EXCLUDED.media_type,
    byte_size = EXCLUDED.byte_size,
    sha256 = EXCLUDED.sha256,
    fetched_at = EXCLUDED.fetched_at,
    updated_at = now()
