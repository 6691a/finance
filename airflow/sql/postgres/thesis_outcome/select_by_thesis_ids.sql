-- 여러 추론의 지평별 결과 전부. Slack T+5 섹션과 그래프 동기화가 읽는다.
--
-- 채점만 있는 행(해설 전), 해설만 있는 행(post_close), 둘 다 있는 행이 섞여 온다.
-- 부르는 쪽이 NULL을 보고 가른다.
SELECT thesis_id,
       horizon_days,
       as_of_at,
       dag_run_id,
       evaluated_at,
       actual_return_pct,
       actual_outcome,
       brier_score,
       narrative,
       verdict,
       narrative_at,
       llm_model,
       prompt_version
FROM thesis_outcome
WHERE thesis_id = ANY(%s)
ORDER BY thesis_id, horizon_days
