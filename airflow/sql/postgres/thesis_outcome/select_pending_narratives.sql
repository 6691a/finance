-- 한 지평에서 아직 해설이 없는 추론 전부. 원 추론의 확률·이유를 함께 준다.
--
-- **예측 슬롯 다섯과 리뷰 둘이 대상이다.** 채점은 예측 슬롯만이지만 해설은 리뷰에도
-- 붙는다. 리뷰는 "오늘 이래서 움직였다"는 인과 주장이라 며칠 뒤 보도로 검증할 값어치가
-- 오히려 크다.
--
-- **post_nxt_close가 2026-08-31에 들어왔다**(`19-nxt-narration.md`). 7단계가 뺐던 이유는
-- 값어치 판단이 아니라 해설 호출이 슬롯을 `pre_open`으로 하드코딩하던 결함이었고, 그것은
-- 2026-08-23에 풀렸다 — 부르는 쪽이 `run_slot` 컬럼으로 슬롯마다 호출을 나눈다. 18단계가
-- 이 리뷰를 장전 프롬프트로 이으면서 해설을 읽는 소비자도 생겼다.
--
-- 슬롯 목록이 리터럴이 아니라 파라미터인 것은 `select_pending_grades.sql`과 같은 형태다.
--
-- **`select_backlog.sql`의 unnarrated FILTER가 같은 목록을 봐야 한다.** 어긋나면 한쪽은
-- 해설을 안 만들고 다른 쪽은 그것을 밀림으로 세서 ops 브리핑이 매일 거짓 경보를 낸다.
--
-- **LEFT JOIN이다.** 리뷰 추론(post_close·post_nxt_close)은 채점을 받지 않아
-- thesis_outcome 행이 아예 없다.
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
       sizing.return_error_pct,
       sizing.predicted_band_pct,
       -- 예측의 축(15단계). **해설 모델이 이 단계가 만들어진 이유의 절반이다** — 전에는
       -- 실제 결과 한 줄뿐이라 어느 축의 등락인지 모델도 알 수 없었다.
       thesis.base_price,
       thesis.base_at,
       thesis.base_return_pct
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
