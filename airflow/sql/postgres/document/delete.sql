-- 제공처가 지운 문서의 행을 지운다(2026-08-31 사용자 결정). 태그·추출·첨부 행은 CASCADE로
-- 함께 사라지고 `source_record`는 남는다 — 발견했다는 사실은 거기 있다.
--
-- 먼저 update_unlink_duplicates.sql로 이 행을 가리키는 중복을 끊어야 한다(RESTRICT).
DELETE FROM document
WHERE id = %s
