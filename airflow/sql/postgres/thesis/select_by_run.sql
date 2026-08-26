-- 한 (run_date, run_slot)의 추론 전부.
--
-- 세 곳이 쓴다. 저장 전 "이미 있는가" 판정(첫 성공본 불변), Slack 발송, 그래프 동기화다.
-- 그래서 `id`와 `dag_run_id`를 함께 준다 — 근거를 붙이려면 id가, 어느 실행이 썼는지 알려면
-- dag_run_id가 필요하다.
--
-- **채점·해설은 여기 없다.** `thesis`는 확률 예측과 그 근거만 갖고 지평별 결과는
-- `thesis_outcome`이 갖는다. 함께 필요해지면 그때 조회를 따로 만든다 — 소비자가 없던
-- `select_by_thesis_ids.sql`은 2026-08-26에 지웠다.
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
       -- 판 7부터의 방향별 조건부 크기. 그 전 행은 NULL이다. **끝에 붙인다** — 읽는 쪽이
       -- 위치로 매핑해서, 가운데 끼우면 뒤 칸이 전부 한 칸씩 밀린다.
       up_return_pct,
       down_return_pct
FROM thesis
WHERE run_date = %s
  AND run_slot = %s
ORDER BY subject_kind, subject_code
