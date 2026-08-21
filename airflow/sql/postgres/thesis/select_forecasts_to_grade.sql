-- 아직 채점하지 않은 장전 전망 전부.
--
-- **날짜 제한이 없다.** 장후 실행이 실패한 날의 forecast도 다음 실행이 회수해야 하기 때문이다.
-- 종가가 영영 나오지 않는 행(상장폐지 등)은 계속 이 결과에 남는다. 스캔이 커지지 않도록
-- `ix_thesis_run_slot_evaluated_at`이 (run_slot, evaluated_at)을 덮는다.
--
-- 채점 자체(분류·Brier 계산)는 SQL이 아니라 `modules/thesis.py`의 순수 함수가 한다.
-- 여기서는 무엇을 채점해야 하는지와 그 계산에 필요한 확률만 준다.
SELECT id,
       run_date,
       subject_kind,
       subject_code,
       prob_up,
       prob_down,
       prob_flat
FROM thesis
WHERE run_slot = 'pre_open'
  AND evaluated_at IS NULL
ORDER BY run_date, subject_kind, subject_code
