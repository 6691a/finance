-- LLM 대화 하나를 `running`으로 연다. 테이블은 백엔드 마이그레이션이 만든다.
--
-- **그래프를 부르기 전에 커밋한다.** 대화가 죽어도 "시작했다"는 사실이 남아야 한다.
-- 실패한 대화가 원장에 없으면 패턴 분석이 성공한 실행만 보게 되고, 그게 지금 상태다.
--
-- **자연키가 없어 upsert도 아니다.** 실패한 대화도 남기고 재시도는 새 대화라, 같은
-- (kind, run_date, run_slot, horizon_days)에 행이 여럿인 것이 정상이다. 이 테이블은
-- 원장이지 판단이 아니라서 "첫 성공본 불변"이 적용되지 않는다.
--
-- 총량 셋(tool_rounds·tool_calls·tool_result_chars)은 여기서 0으로 열고
-- `update_finish.sql`이 채운다.
--
-- 정의의 원본은 `apps/models/analysis/thesis.py`의 `ThesisLlmRun`이고
-- `tests/modules/test_thesis_pipeline.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO thesis_llm_run (
    kind,
    run_date,
    run_slot,
    horizon_days,
    as_of_at,
    dag_run_id,
    try_number,
    llm_model,
    prompt_version,
    started_at,
    status,
    tool_rounds,
    tool_calls,
    tool_result_chars
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'running', 0, 0, 0)
RETURNING id
