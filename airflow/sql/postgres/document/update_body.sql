-- 본문 수집 결과를 채운다. 행은 발견 단계가 이미 만들었으므로 UPDATE다.
--
-- **`content_level`은 본문이 실제로 들어왔을 때만 오른다.** 출처 정책(`collection_mode`)이
-- `full_text`라도 본문을 못 받았으면 이 행에 담긴 것은 여전히 제목과 요약뿐이다.
-- `metadata_only`는 건드리지 않는다 — CHECK 제약이 그 수준에 본문을 금지한다.
--
-- **`content_hash`를 다시 계산하지 않는다.** 해시는 제목과 요약만 보므로 본문이 들어와도
-- 값이 그대로다. 여기서 건드리면 문서 전체가 재평가 대상이 된다.
--
-- `body_status IS NULL` 조건은 두 실행이 같은 문서를 겹쳐 집었을 때 나중 것이 앞의 결과를
-- 덮지 않게 한다.
UPDATE document
SET body = %(body)s,
    body_status = %(body_status)s,
    content_level = CASE
        WHEN %(body)s IS NOT NULL AND content_level <> 'metadata_only' THEN 'full_text'
        ELSE content_level
    END,
    updated_at = now()
WHERE id = %(document_id)s
  AND body_status IS NULL
