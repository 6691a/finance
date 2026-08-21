-- 한 (run_date, run_slot)의 추론 전부.
--
-- 세 곳이 쓴다. 저장 전 "이미 있는가" 판정(첫 성공본 불변), Slack 발송, 그래프 동기화다.
-- 그래서 `id`와 `dag_run_id`를 함께 준다 — 근거를 붙이려면 id가, 어느 실행이 썼는지 알려면
-- dag_run_id가 필요하다.
--
-- 채점 컬럼도 함께 준다. 장후 실행이 그날 아침 forecast의 채점 갱신까지 그래프에 반영한다.
SELECT id,
       run_slot,
       run_date,
       as_of_at,
       dag_run_id,
       subject_kind,
       subject_code,
       label,
       prob_up,
       prob_down,
       prob_flat,
       up_reasoning,
       down_reasoning,
       flat_reasoning,
       tool_rounds,
       llm_model,
       prompt_version,
       evaluated_at,
       actual_return_pct,
       actual_outcome,
       brier_score
FROM thesis
WHERE run_date = %s
  AND run_slot = %s
ORDER BY subject_kind, subject_code
