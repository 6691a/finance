-- 대화 하나를 `running`으로 연다. **모델을 부르기 전에 별도 트랜잭션으로 커밋한다.**
--
-- 실패한 대화가 원장에 없으면 "안 돌았다"와 "돌다 죽었다"를 못 가른다. 그 구분이 없으면
-- 패턴 분석이 성공한 실행만 보게 된다.
INSERT INTO kospi_llm_run (
    kind,
    run_date,
    slot,
    as_of_at,
    status,
    llm_model,
    prompt_version,
    dag_run_id,
    try_number,
    started_at
) VALUES (
    %(kind)s,
    %(run_date)s,
    %(slot)s,
    %(as_of_at)s,
    'running',
    %(llm_model)s,
    %(prompt_version)s,
    %(dag_run_id)s,
    %(try_number)s,
    %(started_at)s
)
RETURNING id
