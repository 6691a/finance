-- 본문 수집 결과를 채운다. 행은 발견 단계가 이미 만들었으므로 UPDATE다.
--
-- **본문이 들어오면 `content_level`을 예외 없이 올린다.** 처음에는 `metadata_only`인 행을
-- 빼 두었는데, 그 수준에는 본문이 있으면 안 된다는 CHECK가 있어서 본문만 쓰고 수준을 안
-- 올리면 `ck_document_metadata_only_has_no_body` 위반으로 태스크가 죽는다(2026-08-30 운영
-- 실측: fss 옛 행 34건). 옛 `metadata_only`는 그 출처의 정책이 그랬던 시절의 흔적이고,
-- 본문을 받은 이상 그 행에 담긴 것은 전문이 맞다. **애초에 본문을 받으면 안 되는 출처는
-- 여기서 거르는 것이 아니라 큐(`select_pending_body.sql`)가 뺀다.**
--
-- **`content_hash`를 다시 계산하지 않는다.** 해시는 제목과 요약만 보므로 본문이 들어와도
-- 값이 그대로다. 여기서 건드리면 문서 전체가 재평가 대상이 된다.
--
-- `body_status IS NULL` 조건은 두 실행이 같은 문서를 겹쳐 집었을 때 나중 것이 앞의 결과를
-- 덮지 않게 한다.
UPDATE document
SET body = %(body)s,
    body_status = %(body_status)s,
    content_level = CASE WHEN %(body)s IS NOT NULL THEN 'full_text' ELSE content_level END,
    updated_at = now()
WHERE id = %(document_id)s
  AND body_status IS NULL
