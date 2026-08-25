-- 추론 한 건을 저장한다. 테이블은 백엔드 마이그레이션이 만든다.
--
-- **upsert가 아니라 INSERT다.** 같은 (run_date, run_slot, subject_kind, subject_code)에 행이
-- 이미 있으면 아무 것도 바꾸지 않고 0행을 돌려준다. LLM은 재호출마다 답이 달라서 덮어쓰면
-- 최초 판단이 사라지고, 옛 확률로 매긴 Brier가 새 확률 옆에 남는다. 첫 성공본이 불변인 이유다.
--
-- 부르는 쪽은 이 문장 전에 `select_by_run.sql`로 먼저 확인해 LLM 호출 자체를 건너뛴다.
-- 여기서 0행이 나오는 것은 그 확인과 INSERT 사이에 다른 실행이 먼저 넣은 경우다.
--
-- 채점 컬럼 넷은 여기서 채우지 않는다. `update_outcome.sql`이 나중에 채운다.
--
-- 정의의 원본은 `apps/models/analysis/thesis.py`의 `Thesis`이고
-- `tests/modules/test_thesis.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO thesis (
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
    input_state,
    tool_rounds,
    llm_model,
    prompt_version
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT uq_thesis_natural_key DO NOTHING
RETURNING id
