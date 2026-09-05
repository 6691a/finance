-- 원인을 아직 못 찾은 급변 중 기한이 남은 것.
--
-- **기한이 지난 것은 여기서 안 준다.** 닫는 일은 별도 쿼리(`close_expired_causes.sql`)가
-- 하고, 이 목록은 "지금 모델을 부를 대상"만 갖는다. 둘을 한 쿼리로 묶으면 부르는 쪽이
-- 행마다 다시 판정해야 한다.
--
-- `cause_deadline IS NULL`도 대상이다 — 포착 시각에 달력이 아직 그날까지 안 채워진
-- 경우이고, 부르는 쪽이 이번에 다시 구해 채운다. 기한을 모른다고 원인 찾기를 미루면
-- 달력 수집이 밀린 날의 사건이 영영 안 돌아본다.
SELECT id,
       symbol,
       session_date,
       direction,
       detected_at,
       window_start,
       window_end,
       extreme_at,
       extreme_price,
       trigger_price,
       move_pct,
       window_change_pct,
       peers,
       cause_deadline,
       cause_attempts
FROM market_shock_event
WHERE cause_status = 'pending'
  AND (cause_deadline IS NULL OR cause_deadline >= %(today)s)
ORDER BY detected_at
LIMIT %(limit)s
