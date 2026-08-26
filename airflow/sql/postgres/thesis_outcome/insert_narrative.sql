-- 한 (추론, 지평)의 사후 해설과 판정을 쓴다.
--
-- **이미 쓴 해설을 덮지 않는다.** `WHERE thesis_outcome.narrative IS NULL`이 그것을 강제한다.
-- 첫 성공본 불변은 추론·채점과 같다 — 지평마다 한 번이고, T+5 해설이 마음에 안 들어도
-- 고치지 않는다.
--
-- 순수 UPDATE가 아니라 upsert인 이유: **post_close 추론은 채점을 받지 않아 행이 없다.**
-- 그때는 해설이 행을 새로 만든다. 채점 칸 넷은 NULL로 남고 CHECK가 그것을 허용한다
-- (`ck_thesis_outcome_grade_all_or_none`, `ck_thesis_outcome_not_empty`).
--
-- 지평 0은 오지 않는다. 그날의 후속 보도가 아직 쌓이지 않아 해설을 쓸 재료가 없고,
-- `ck_thesis_outcome_zero_horizon_has_no_narrative`가 DB에서 한 번 더 막는다.
INSERT INTO thesis_outcome (
    thesis_id,
    horizon_days,
    as_of_at,
    dag_run_id,
    narrative,
    verdict,
    narrative_at,
    llm_model,
    prompt_version,
    narration_run_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT uq_thesis_outcome_natural_key DO UPDATE SET
    narrative = EXCLUDED.narrative,
    verdict = EXCLUDED.verdict,
    narrative_at = EXCLUDED.narrative_at,
    llm_model = EXCLUDED.llm_model,
    prompt_version = EXCLUDED.prompt_version,
    narration_run_id = EXCLUDED.narration_run_id,
    updated_at = now()
WHERE thesis_outcome.narrative IS NULL
