-- 찾은 원인을 쓰고 닫는다. **첫 성공본은 불변이다.**
--
-- `cause_status = 'pending'`을 조건에 두어, 이미 닫힌 행을 다시 쓰지 않는다. 재시도가
-- 같은 이벤트를 두 번 풀어도 먼저 쓴 판단이 남는다 — 저장소 규칙이다.
--
-- `found=false`로 끝난 시도는 이 쿼리를 안 부른다. 그때는 `pending`이 그대로 남아
-- 다음날이 다시 본다.
UPDATE market_shock_event
SET cause_status = 'resolved',
    cause_text = %(cause_text)s,
    cause_kind = %(cause_kind)s,
    cause_document_ids = %(cause_document_ids)s::jsonb,
    cause_search_used = %(cause_search_used)s,
    cause_weak = %(cause_weak)s,
    cause_prompt_version = %(prompt_version)s,
    cause_llm_model = %(llm_model)s,
    cause_resolved_at = %(resolved_at)s
WHERE id = %(id)s
  AND cause_status = 'pending'
RETURNING id
