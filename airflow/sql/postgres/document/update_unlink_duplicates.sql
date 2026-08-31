-- 대표 문서를 지우기 전에 그것을 가리키던 중복의 연결을 끊는다.
--
-- `canonical_document_id`는 ON DELETE RESTRICT라 끊지 않으면 DELETE가 막힌다. 끊긴 중복은
-- 다시 대표가 되어 본문·평가 큐에 선다 — 같은 리포트의 첫 게시(39973)가 살아 있는데 나중
-- 게시(40006)가 대표로 뽑힌 뒤 지워진 것이 실제 사례다(2026-08-31).
UPDATE document
SET canonical_document_id = NULL,
    updated_at = now()
WHERE canonical_document_id = %s
