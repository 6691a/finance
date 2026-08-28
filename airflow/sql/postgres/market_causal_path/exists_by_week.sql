-- 그 주에 경로가 이미 있나. **재실행 판정이 이것이다**(설계 §5.4).
--
-- 있으면 LLM을 다시 부르지 않는다. 첫 성공본이 불변이라 다시 부르면 최초 판단이 사라진다.
-- 같은 규칙이 `thesis/select_same_day.sql`에 이미 있다.
--
-- `input_hash`로 판정하지 않는 이유는 그것이 감사 값이기 때문이다 — 후보가 조금 달라진
-- 재실행이 같은 주에 행을 한 벌 더 만들면 안 된다.
SELECT EXISTS (
    SELECT 1 FROM market_causal_path WHERE week_start = %(week_start)s
)
