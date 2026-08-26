-- 한 지평에서 아직 해설이 없는 추론 전부. 원 추론의 확률·이유를 함께 준다.
--
-- **예측 슬롯 다섯과 post_close가 대상이다.** 채점은 예측 슬롯만이지만 해설은 post_close
-- 리뷰에도 붙는다. 장후 리뷰는 "오늘 이래서 움직였다"는 인과 주장이라 며칠 뒤 보도로
-- 검증할 값어치가 크다.
--
-- **post_nxt_close는 뺀다**(2026-08-22). NXT 애프터마켓 리뷰는 해설 루프에 아직 넣지
-- 않았다. 넣으려면 `thesis_state.NARRATED_SLOTS`에 더하면 된다 — 부르는 쪽은
-- `run_slot` 컬럼으로 슬롯마다 호출을 나눈다(2026-08-23). 슬롯 목록이 리터럴이 아니라
-- 파라미터인 것은 `select_pending_grades.sql`과 같은 형태다.
--
-- **`select_backlog.sql`의 unnarrated FILTER가 같은 목록을 봐야 한다.** 어긋나면 한쪽은
-- 해설을 안 만들고 다른 쪽은 그것을 밀림으로 세서 ops 브리핑이 매일 거짓 경보를 낸다.
--
-- **LEFT JOIN이다.** post_close 추론은 채점을 받지 않아 thesis_outcome 행이 아예 없다.
-- INNER JOIN으로 걸면 그 추론들이 영영 해설을 못 받는다. 행이 없으면 해설이 새로 만든다.
--
-- 채점 값 넷과 지평 0의 크기 오차를 함께 주지만 프롬프트에 실을지는 부르는 쪽이 정한다
-- (informed/blind 변형). 여기서는 있는 대로 준다.
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
       outcome.brier_score,
       -- 크기 채점은 **지평 0에만** 있다. 해설은 지평 1·3·5라 지평을 건너 조인해야 한다.
       -- 숫자만으로는 과대·과소의 이유가 안 남아서, 크게 어긋난 날은 해설이 그것을 다루게 한다.
       sizing.predicted_return_pct,
       sizing.return_error_pct
FROM thesis
LEFT JOIN thesis_outcome AS outcome
       ON outcome.thesis_id = thesis.id
      AND outcome.horizon_days = %s
LEFT JOIN thesis_outcome AS sizing
       ON sizing.thesis_id = thesis.id
      AND sizing.horizon_days = 0
WHERE thesis.run_date = %s
  AND thesis.run_slot = ANY(%s)
  AND outcome.narrative IS NULL
ORDER BY thesis.run_slot, thesis.subject_kind, thesis.subject_code
