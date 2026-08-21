-- 추론 품질 지표. ops 브리핑이 읽는다.
--
-- **시장 브리핑에 싣지 않는다**(2026-08-21 결정). 읽는 사람이 다르다 — 오늘 전망은 시장을
-- 보는 사람이 읽고, "우리 추론이 잘 맞고 있나"는 운영자가 본다.
--
-- 지평별로 한 행이다. 지평을 섞으면 T+1의 잡음이 T+5의 신호를 덮는다.
--
-- 균등 확률(1/3씩)의 Brier는 결과와 무관하게 약 0.667이다. 그것이 baseline이고 비교는
-- 부르는 쪽이 한다 — 상수 하나를 SQL과 파이썬 두 곳에 두지 않는다.
--
-- 창 밖의 오래된 추론은 세지 않는다. 프롬프트와 모델이 바뀌면 옛 점수와 섞여 추이가
-- 흐려진다. 구간은 파라미터로 받는다.
SELECT outcome.horizon_days,
       count(*) FILTER (WHERE outcome.evaluated_at IS NOT NULL) AS graded,
       avg(outcome.brier_score) FILTER (WHERE outcome.evaluated_at IS NOT NULL) AS mean_brier,
       count(*) FILTER (WHERE outcome.actual_outcome = 'flat') AS flat_outcomes,
       count(*) FILTER (WHERE outcome.narrative IS NOT NULL) AS narrated,
       count(*) FILTER (WHERE outcome.verdict = 'supported') AS supported,
       count(*) FILTER (WHERE outcome.verdict = 'contradicted') AS contradicted,
       count(*) FILTER (WHERE outcome.verdict = 'unresolved') AS unresolved
FROM thesis_outcome AS outcome
JOIN thesis ON thesis.id = outcome.thesis_id
WHERE thesis.run_date >= %s
GROUP BY outcome.horizon_days
ORDER BY outcome.horizon_days
