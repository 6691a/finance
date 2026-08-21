-- 채점·해설이 밀린 건수. ops 브리핑이 읽는다.
--
-- 밀림은 정상 흐름이기도 하다 — T+5는 5영업일이 지나야 채점되고, 그동안은 당연히 미채점이다.
-- **그러니 "목표일이 지났는데도 안 된 것"만 센다.** 그렇지 않으면 매일 같은 숫자가 떠서
-- 아무도 안 보게 된다.
--
-- 목표 영업일은 `market_session`이 정한다. 달력이 그날까지 안 채워졌으면 그 조합은 아직
-- 지나지 않은 것으로 본다(0행이라 조인에서 빠진다).
WITH due AS (
    SELECT thesis.id AS thesis_id,
           thesis.run_slot,
           horizon.horizon_days,
           (
               SELECT session_date
               FROM market_session
               WHERE market_code = 'KRX'
                 AND session_date >= thesis.run_date
                 AND effective_open_day
               ORDER BY session_date
               OFFSET horizon.horizon_days
               LIMIT 1
           ) AS target_date
    FROM thesis
    CROSS JOIN unnest(%s::integer[]) AS horizon(horizon_days)
    WHERE thesis.run_date >= %s
)
SELECT
    count(*) FILTER (
        WHERE due.run_slot = 'pre_open'
          AND outcome.evaluated_at IS NULL
    ) AS ungraded,
    count(*) FILTER (
        WHERE due.horizon_days <> 0
          AND outcome.narrative IS NULL
    ) AS unnarrated
FROM due
LEFT JOIN thesis_outcome AS outcome
       ON outcome.thesis_id = due.thesis_id
      AND outcome.horizon_days = due.horizon_days
WHERE due.target_date IS NOT NULL
  AND due.target_date < %s
