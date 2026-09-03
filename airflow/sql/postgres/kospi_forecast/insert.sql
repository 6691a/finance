-- 전망 하나를 쓴다. **첫 성공본은 불변이다.**
--
-- 같은 `(run_date, slot)`에 행이 있으면 아무 것도 바꾸지 않는다. LLM은 재호출마다 답이
-- 달라서 덮어쓰면 최초 판단이 사라진다. 잘못된 전망도 고치지 않는다 — 승인·보류 상태
-- 머신도, 사람이 행을 UPDATE하는 경로도 없다.
--
-- 채점 칸 넷은 여기서 쓰지 않는다. 장후에 `update_grade.sql`이 한 번 채운다.
INSERT INTO kospi_forecast (
    run_date,
    slot,
    as_of_at,
    base_price,
    base_at,
    so_far_pct,
    direction,
    expected_change_pct,
    band_pct,
    reasons,
    weak,
    rejected_reasons,
    input_state,
    prompt_version,
    llm_model,
    dag_run_id,
    llm_run_id
) VALUES (
    %(run_date)s,
    %(slot)s,
    %(as_of_at)s,
    %(base_price)s,
    %(base_at)s,
    %(so_far_pct)s,
    %(direction)s,
    %(expected_change_pct)s,
    %(band_pct)s,
    %(reasons)s,
    %(weak)s,
    %(rejected_reasons)s,
    %(input_state)s,
    %(prompt_version)s,
    %(llm_model)s,
    %(dag_run_id)s,
    %(llm_run_id)s
)
ON CONFLICT ON CONSTRAINT uq_kospi_forecast_natural_key DO NOTHING
RETURNING id
