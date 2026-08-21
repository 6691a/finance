-- 지수의 T+N 누적 등락률. 다지평 채점이 읽는다.
--
-- **기준가는 지평이 달라도 같다** — 예측일 마감 봉의 `previous_close`, 즉 예측일 전 영업일
-- 종가다. 지평마다 기준가를 옮기면 누적이 연속되지 않아 T+1과 T+5를 비교할 수 없다.
--
-- 종가는 마감 봉(15:30 KST 시작)의 close다. `kis_quote_intraday`가 `*/5 8-16`으로 돌아
-- 16:00이면 확정이다.
--
-- **봉 시각 둘을 파라미터로 받는다.** 세션 날짜에서 마감 봉 시각(UTC)을 만드는 일은 KST
-- 경계 계산이라 파이썬이 한다. SQL에 시간대 변환을 넣으면 컨테이너 설정에 따라 조용히
-- 달라진다.
--
-- 봉이 없으면 그 지수는 결과에 없다. 부르는 쪽은 미채점으로 남긴다.
WITH bounds AS (
    SELECT %s::timestamptz AS base_bar_at,
           %s::timestamptz AS target_bar_at
)
SELECT target.symbol,
       bounds.target_bar_at,
       base.previous_close AS base_close,
       target.close AS target_close,
       (target.close - base.previous_close) / base.previous_close * 100 AS return_pct
FROM index_bar AS target
CROSS JOIN bounds
JOIN index_bar AS base
  ON base.provider = target.provider
 AND base.symbol = target.symbol
 AND base.bar_at = bounds.base_bar_at
WHERE target.provider = 'kis'
  AND target.bar_at = bounds.target_bar_at
  AND target.symbol = ANY(%s)
  AND base.previous_close <> 0
ORDER BY target.symbol
