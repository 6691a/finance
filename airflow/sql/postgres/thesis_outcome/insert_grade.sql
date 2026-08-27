-- 한 (추론, 지평)의 채점을 쓴다.
--
-- **이미 매긴 점수를 덮지 않는다.** `WHERE thesis_outcome.evaluated_at IS NULL`이 그것을
-- 강제한다. 첫 성공본 불변은 추론뿐 아니라 채점에도 적용된다 — 같은 지평을 다시 채점해
-- 값이 달라지면 어느 쪽이 그날의 판단이었는지 알 수 없다.
--
-- `DO NOTHING`이 아니라 조건부 `DO UPDATE`인 이유: 해설이 먼저 성공해 행을 만들어 둔
-- 경우가 있다. 채점이 종가 결측으로 실패한 날 해설만 돌면 그렇게 된다. 그때 `DO NOTHING`이면
-- 그 지평은 영영 채점되지 않는다.
--
-- **크기 채점 둘은 방향 채점과 같은 트랜잭션·같은 행이다**(판 7부터). 실현이 `flat`이거나
-- 그 방향의 추정이 없으면 NULL이고, 그것을 정하는 것은 `thesis.domain.return_error`다.
INSERT INTO thesis_outcome (
    thesis_id,
    horizon_days,
    as_of_at,
    dag_run_id,
    evaluated_at,
    actual_return_pct,
    actual_outcome,
    brier_score,
    predicted_return_pct,
    return_error_pct
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT uq_thesis_outcome_natural_key DO UPDATE SET
    evaluated_at = EXCLUDED.evaluated_at,
    actual_return_pct = EXCLUDED.actual_return_pct,
    actual_outcome = EXCLUDED.actual_outcome,
    brier_score = EXCLUDED.brier_score,
    predicted_return_pct = EXCLUDED.predicted_return_pct,
    return_error_pct = EXCLUDED.return_error_pct,
    updated_at = now()
WHERE thesis_outcome.evaluated_at IS NULL
