-- 한 지평에서 아직 해설이 없는 추론 전부. 원 추론의 확률·이유를 함께 준다.
--
-- **pre_open과 post_close 둘이 대상이다.** 채점은 pre_open만이지만 해설은 post_close
-- 리뷰에도 붙는다. 장후 리뷰는 "오늘 이래서 움직였다"는 인과 주장이라 며칠 뒤 보도로
-- 검증할 값어치가 크다.
--
-- **post_nxt_close는 뺀다**(2026-08-22). NXT 애프터마켓 리뷰는 해설 루프에 아직 넣지
-- 않았다 — 넣으려면 `NarrativeTarget`이 슬롯을 들고 슬롯마다 호출을 나눠야 한다
-- (`FollowupNarrator`의 프롬프트 첫 줄이 슬롯 하나를 전제한다). 슬롯을 열거하는 것은
-- `select_pending_grades.sql`이 pre_open을 리터럴로 적는 것과 같은 형태다.
--
-- **`select_backlog.sql`의 unnarrated FILTER가 같은 목록을 봐야 한다.** 어긋나면 한쪽은
-- 해설을 안 만들고 다른 쪽은 그것을 밀림으로 세서 ops 브리핑이 매일 거짓 경보를 낸다.
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
  AND thesis.run_slot IN ('pre_open', 'post_close')
  AND outcome.narrative IS NULL
ORDER BY thesis.run_slot, thesis.subject_kind, thesis.subject_code
