-- 추론마다 상위 근거 몇 개. Slack 발송이 읽는다.
--
-- 상한을 SQL이 정하는 이유는 추론 수 × 근거 수만큼 행을 가져와 파이썬에서 자르면 발송에
-- 필요 없는 근거까지 매번 실어 오기 때문이다. 개수는 파라미터로 받는다.
--
-- **인용 주체마다 따로 센다.** 원 추론(`outcome_horizon_days IS NULL`)과 지평별 해설이 같은
-- 테이블에 있어, 파티션에 그 칸을 넣지 않으면 해설 근거가 원 추론의 상위 자리를 먹는다.
-- 어느 주체의 근거를 원하는지는 파라미터로 받는다 — `NULL`이면 원 추론의 것만이다.
--
-- `direction`·`mechanism`은 Slack이 채택 방향의 근거만 골라 그리는 데 쓴다. 그 거르기는
-- 파이썬이 한다 — 어느 방향이 채택됐는지는 `thesis`의 세 확률을 비교해야 알고, 그 판단을
-- 이 쿼리에 넣으면 조인이 하나 더 는다. 그래서 상한을 표시 개수보다 넉넉히 받는다.
SELECT thesis_id,
       outcome_horizon_days,
       evidence_kind,
       evidence_ref,
       evidence_title,
       evidence_url,
       rank,
       direction,
       mechanism
FROM (
    SELECT thesis_id,
           outcome_horizon_days,
           evidence_kind,
           evidence_ref,
           evidence_title,
           evidence_url,
           rank,
           direction,
           mechanism,
           row_number() OVER (PARTITION BY thesis_id, outcome_horizon_days ORDER BY rank) AS position
    FROM thesis_evidence
    WHERE thesis_id = ANY(%s)
      AND outcome_horizon_days IS NOT DISTINCT FROM %s
) AS ranked
WHERE position <= %s
ORDER BY thesis_id, rank
