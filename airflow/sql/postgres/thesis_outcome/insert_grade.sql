-- 한 (추론, 지평)의 채점을 쓴다.
--
-- **이미 매긴 점수를 덮지 않는다.** `WHERE thesis_outcome.evaluated_at IS NULL`이 그것을
-- 강제한다. 첫 성공본 불변은 추론뿐 아니라 채점에도 적용된다 — 같은 지평을 다시 채점해
-- 값이 달라지면 어느 쪽이 그날의 판단이었는지 알 수 없다.
--
-- `DO NOTHING`이 아니라 조건부 `DO UPDATE`인 이유: 해설이 먼저 성공해 행을 만들어 둔
-- 경우가 있다. 채점이 종가 결측으로 실패한 날 해설만 돌면 그렇게 된다. 그때 `DO NOTHING`이면
-- 그 지평은 영영 채점되지 않는다.
INSERT INTO thesis_outcome (
    thesis_id,
    horizon_days,
    as_of_at,
    dag_run_id,
    evaluated_at,
    actual_return_pct,
    actual_outcome,
    brier_score
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT uq_thesis_outcome_natural_key DO UPDATE SET
    evaluated_at = EXCLUDED.evaluated_at,
    actual_return_pct = EXCLUDED.actual_return_pct,
    actual_outcome = EXCLUDED.actual_outcome,
    brier_score = EXCLUDED.brier_score,
    updated_at = now()
WHERE thesis_outcome.evaluated_at IS NULL
