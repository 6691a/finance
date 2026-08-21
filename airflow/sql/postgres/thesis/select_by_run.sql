-- 한 (run_date, run_slot)의 추론 전부.
--
-- 세 곳이 쓴다. 저장 전 "이미 있는가" 판정(첫 성공본 불변), Slack 발송, 그래프 동기화다.
-- 그래서 `id`와 `dag_run_id`를 함께 준다 — 근거를 붙이려면 id가, 어느 실행이 썼는지 알려면
-- dag_run_id가 필요하다.
--
-- **채점·해설은 여기 없다.** `thesis`는 확률 예측과 그 근거만 갖고 지평별 결과는
-- `thesis_outcome`이 갖는다. 함께 필요하면 `thesis_outcome/select_by_thesis_ids.sql`을
-- 따로 부른다.
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
       prompt_version
FROM thesis
WHERE run_date = %s
  AND run_slot = %s
ORDER BY subject_kind, subject_code
