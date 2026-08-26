-- 장중 추론의 T+N 누적 등락률(지수).
--
-- **기준가가 `select_horizon_return.sql`과 다르다.** 저쪽은 예측일 전 영업일 종가
-- (`previous_close`)를 분모로 쓰는데, 장중 추론은 "지금 이 가격에서 어디로 가나"를 맞히므로
-- 분모가 그 슬롯 `as_of_at` 직전 봉의 종가다. 기존 파일에 분기를 얹지 않고 파일을 나눈다 —
-- 잘 돌고 있는 `pre_open` 채점 경로가 조용히 따라 바뀌면 안 된다.
--
-- **기준가는 파라미터로 받는다.** 봉에서 다시 뽑지 않는 이유는 그 사이 `kis_quote_intraday`
-- 재실행이 없던 봉을 채워 넣어 "직전 봉"이 달라질 수 있기 때문이다. 부르는 쪽은 추론 행의
-- `input_state`에 박혀 있는 값, 즉 **모델이 실제로 본 가격**을 준다. 첫 성공본 불변과 같은
-- 이유다.
--
-- **기준가가 지평마다 같다.** 지평이 달라도 분모는 그 슬롯의 기준가 하나다. 지평마다
-- 기준가를 옮기면 누적이 연속되지 않아 T+1과 T+5를 비교할 수 없다.
--
-- 목표가는 그 영업일 마감 봉(15:30 KST 시작)의 close다. 봉이 없으면 그 지수는 결과에 없고
-- 부르는 쪽은 미채점으로 남긴다.
WITH base AS (
    SELECT symbol, price
    FROM unnest(%s::text[], %s::numeric[]) AS given(symbol, price)
    WHERE price <> 0
)
SELECT target.symbol,
       base.price AS base_close,
       target.close AS target_close,
       (target.close - base.price) / base.price * 100 AS return_pct
FROM index_bar AS target
JOIN base ON base.symbol = target.symbol
WHERE target.provider = 'kis'
  AND target.bar_at = %s
ORDER BY target.symbol
