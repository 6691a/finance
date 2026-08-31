-- 한 대상의 지난 추론과 그 지평별 채점·해설. 장전이 프롬프트에 미리 싣는 조회이고
-- `past_theses` 툴도 같은 것을 읽는다(`thesis.past_theses`).
--
-- 장전 추론이 자기 과거 예측과 결과를 돌아보게 하는 것이 이 조회의 목적이다.
-- 피드백 루프는 이 조회 하나이고, 무엇을 보여 줬는지는 `thesis_precedent`가 남긴다.
-- 첫 컬럼 `thesis.id`가 그 엣지의 끝이다.
--
-- **슬롯 목록은 파라미터다**(2026-08-25 둘로 시작, 2026-08-26 장중 넷 추가,
-- 2026-08-31 애프터마켓 리뷰 추가). 예측 슬롯은 그날의 예측이라 채점이 붙고, `post_close`는
-- 장이 닫힌 뒤의 해석이라 채점 없이 해설·판정만 붙는다. 장후 해설은 "그 인과 주장이 이후
-- 보도로 지지됐나"를 담고 있어 다음 예측이 볼 값어치가 크다. 그 전까지는 장후 해설이
-- Slack T+5 섹션과 그래프로만 나가고 예측으로 돌아오지 않았다.
--
-- **`post_nxt_close`는 채점도 해설도 없어 `outcomes`가 빈 배열이다.** `LEFT JOIN`이라 행은
-- 그대로 오고 배열만 빈다. 그래도 싣는 것은 그 본문이 "정규장이 닫힌 뒤 무슨 재료가
-- 나왔나"를 담고 있기 때문이다. 원본은 `thesis.state.PRECEDENT_SLOTS`이고, 해설 루프가 보는
-- `NARRATED_SLOTS`와 다른 목록이다 — 이 슬롯은 해설을 받지 않는다.
--
-- **건수 상한은 슬롯마다 적용한다.** 하나로 묶어 자르면 장후가 섞여 들어온 만큼 장전 예측
-- 목록이 줄어, 슬롯을 늘린 것이 조용히 예측 이력을 짧게 만든다. 뒤집어 말하면 **슬롯이
-- 늘면 총 행 수가 슬롯 수만큼 는다** — 장중 넷이 붙으면서 슬롯당 상한
-- (`PREFETCHED_PAST_THESES`)을 내린 이유가 이것이다.
--
-- **창의 끝은 여기서도 as_of_at이다.** 이것이 없으면 장전 슬롯을 오후에 재실행할 때
-- 그날 저녁의 채점 결과가 아침 예측에 섞인다. 술어를 셋 다 건다.
--   - 추론일이 기준 시각의 KST 날짜보다 앞선 것만(같은 날 아침 것은 아직 결과가 없다)
--   - 채점은 그 시각 이전에 매긴 것만
--   - 해설은 그 시각 이전에 쓴 것만
--
-- 지평별 결과를 배열로 접어 추론당 한 행을 지킨다. 조인으로 펼치면 지평 수만큼 같은
-- 추론이 나온다(`select_briefing_candidates.sql`과 같은 이유).
WITH bounds AS (
    SELECT %s::timestamptz AS as_of_at
),
past AS (
    SELECT thesis.id,
           thesis.run_slot,
           thesis.run_date,
           thesis.prob_up,
           thesis.prob_down,
           thesis.prob_flat,
           thesis.up_reasoning,
           thesis.down_reasoning,
           thesis.flat_reasoning,
           coalesce(
               jsonb_agg(
                   jsonb_build_object(
                       'horizon_days', outcome.horizon_days,
                       'actual_return_pct', outcome.actual_return_pct,
                       'actual_outcome', outcome.actual_outcome,
                       'brier_score', outcome.brier_score,
                       'verdict', outcome.verdict,
                       'narrative', outcome.narrative
                   )
                   ORDER BY outcome.horizon_days
               ) FILTER (WHERE outcome.id IS NOT NULL),
               '[]'::jsonb
           ) AS outcomes,
           row_number() OVER (
               PARTITION BY thesis.run_slot
               ORDER BY thesis.run_date DESC
           ) AS slot_rank
    FROM thesis
    CROSS JOIN bounds
    LEFT JOIN thesis_outcome AS outcome
           ON outcome.thesis_id = thesis.id
          AND (outcome.evaluated_at IS NULL OR outcome.evaluated_at <= bounds.as_of_at)
          AND (outcome.narrative_at IS NULL OR outcome.narrative_at <= bounds.as_of_at)
    WHERE thesis.run_slot = ANY(%s)
      AND thesis.subject_code = %s
      AND thesis.run_date < (bounds.as_of_at AT TIME ZONE 'Asia/Seoul')::date
    GROUP BY thesis.id, thesis.run_slot, thesis.run_date
)
SELECT id,
       run_slot,
       run_date,
       prob_up,
       prob_down,
       prob_flat,
       up_reasoning,
       down_reasoning,
       flat_reasoning,
       outcomes
FROM past
WHERE slot_rank <= %s
ORDER BY run_date DESC, run_slot
