-- 전망 하나를 채점한다. **한 번만 쓴다.**
--
-- `graded_at IS NULL`을 조건에 거는 것이 그 장치다. 재실행이 같은 행을 다시 채점하면
-- 값은 같겠지만 "언제 채점했나"가 흔들리고, 채점 코드가 바뀐 날 옛 행이 조용히 새 규칙으로
-- 덮인다.
--
-- 채점은 순수 함수가 하고 여기는 결과 넷을 쓰기만 한다.
UPDATE kospi_forecast
SET actual_change_pct = %(actual_change_pct)s,
    hit = %(hit)s,
    within_band = %(within_band)s,
    graded_at = %(graded_at)s,
    updated_at = now()
WHERE run_date = %(run_date)s
  AND slot = %(slot)s
  AND graded_at IS NULL
RETURNING id
