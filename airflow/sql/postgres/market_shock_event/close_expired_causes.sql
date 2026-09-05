-- 기한이 지난 급변의 원인 찾기를 닫는다.
--
-- **안 닫으면 매일 같은 LLM 호출이 영원히 돈다.** 3영업일을 다 쓰고 못 찾은 것은 "아무도
-- 안 썼다"가 답이고, 그 사실 자체가 기록할 값어치가 있다.
--
-- `RETURNING`으로 이번에 닫힌 것만 준다. 부르는 쪽이 그것만 Slack에 싣는다 — 재실행이
-- 같은 메시지를 다시 보내지 않는다.
UPDATE market_shock_event
SET cause_status = 'unknown',
    cause_resolved_at = %(resolved_at)s
WHERE cause_status = 'pending'
  AND cause_deadline IS NOT NULL
  AND cause_deadline < %(today)s
RETURNING id, symbol, session_date, direction, detected_at, move_pct, cause_attempts
