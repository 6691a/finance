-- 오늘 **앞 슬롯**의 추론 전부. 장중 슬롯이 자기 아침·직전 판단을 되짚는 조회다.
--
-- `select_past_with_outcomes.sql`을 재사용하지 않는다. 저쪽은 `run_date < 오늘`로 당일을
-- 통째로 막고 있고(그래야 아침 예측에 그날 저녁 채점이 안 섞인다), 그 술어를 완화하면
-- 장전 피드백 루프가 조용히 따라 바뀐다. 조회 목적이 다르면 파일도 다르다.
--
-- **채점 조인이 없다.** 당일 결과는 아직 없다 — 확정 종가가 18:10에 들어오고 지평 채점은
-- 장후 실행이 한다. 그 슬롯 이후 실제로 얼마나 움직였는지는 부르는 쪽이 봉에서 만든다.
--
-- 창의 끝은 여기서도 `as_of_at`이다. 슬롯을 나중에 다시 돌려도 그때 없던 판단이 섞이지
-- 않는다. `run_date`가 아니라 `as_of_at`으로 거는 이유는 같은 날 안에서 순서를 가리는
-- 축이 그것뿐이기 때문이다.
SELECT run_slot,
       as_of_at,
       prob_up,
       prob_down,
       prob_flat,
       up_reasoning,
       down_reasoning,
       flat_reasoning
FROM thesis
WHERE run_date = %s
  AND subject_kind = %s
  AND subject_code = %s
  AND as_of_at < %s
ORDER BY as_of_at
