-- 한 지평에서 아직 해설이 없는 추론 전부. 원 추론의 확률·이유를 함께 준다.
--
-- **두 슬롯 모두 대상이다.** 채점은 pre_open만이지만 해설은 post_close 리뷰에도 붙는다.
-- 장후 리뷰는 "오늘 이래서 움직였다"는 인과 주장이라 며칠 뒤 보도로 검증할 값어치가 크다.
--
-- **LEFT JOIN이다.** post_close 추론은 채점을 받지 않아 thesis_outcome 행이 아예 없다.
-- INNER JOIN으로 걸면 그 추론들이 영영 해설을 못 받는다. 행이 없으면 해설이 새로 만든다.
--
-- 채점 값 넷을 함께 주지만 프롬프트에 실을지는 부르는 쪽이 정한다(informed/blind 변형).
-- 여기서는 있는 대로 준다.
--
-- 날짜 상한이 없다. 해설 LLM이 실패했던 날의 것도 다음 실행이 회수한다.
SELECT thesis.id,
       thesis.run_date,
       thesis.run_slot,
       thesis.subject_kind,
       thesis.subject_code,
       thesis.label,
       thesis.prob_up,
       thesis.prob_down,
       thesis.prob_flat,
       thesis.up_reasoning,
       thesis.down_reasoning,
       thesis.flat_reasoning,
       outcome.actual_return_pct,
       outcome.actual_outcome,
       outcome.brier_score
FROM thesis
LEFT JOIN thesis_outcome AS outcome
       ON outcome.thesis_id = thesis.id
      AND outcome.horizon_days = %s
WHERE thesis.run_date = %s
  AND outcome.narrative IS NULL
ORDER BY thesis.run_slot, thesis.subject_kind, thesis.subject_code
