-- 국내 지수의 **일별** 종가. 사건 전 구간부터 반응 끝까지.
--
-- 실현 등락(`causal/select_index_returns.sql`)이 세 숫자로 접어 주는 것을 여기서 편다.
-- 모델이 주 안에서의 경로와 **사건 전 움직임**을 봐야 선반영을 말할 수 있다(설계 §9).
--
-- **`as_of_at` cutoff를 걸지 않는다.** 실현 등락과 같은 이유다 — 가격은 확정값이고
-- 이 흐름은 반응이 끝난 뒤에 돈다. 근거(문서·공시)만 cutoff를 건다.
--
-- 창의 시작과 끝은 부르는 쪽이 넘긴다. 시작은 `days_before`가 정하고 끝은 반응 주 금요일이다.
SELECT business_date, close
FROM index_daily
WHERE symbol = %(code)s
  AND business_date BETWEEN %(start)s AND %(end)s
  AND close IS NOT NULL
ORDER BY business_date
