-- 장중 추론의 T+N 누적 등락률(종목).
--
-- 규칙은 `index_bar/select_intraday_horizon_return.sql`과 같다. 기준가는 그 슬롯이 본
-- 봉의 종가이고 파라미터로 받는다. 목표가는 **확정 종가**다 — 봉이 아니라 이 표를 보는
-- 이유는 `select_horizon_return.sql`과 같다. 장 마감 뒤 정정이 반영된 값이 여기 있다.
--
-- 이 파일이 `stock_bar`가 아니라 여기 있는 것은 **목표가의 표가 여기**이기 때문이다.
-- 기준가는 이미 파라미터라 어느 표에서 왔는지를 이 조회가 알 필요가 없다.
--
-- 확정 종가가 없으면 그 종목은 결과에 없고 부르는 쪽은 미채점으로 남긴다.
WITH base AS (
    SELECT stock_code, price
    FROM unnest(%s::text[], %s::numeric[]) AS given(stock_code, price)
    WHERE price <> 0
)
SELECT target.stock_code,
       base.price AS base_close,
       target.close_price AS target_close,
       (target.close_price - base.price) / base.price * 100 AS return_pct
FROM stock_investor_trade_daily AS target
JOIN base ON base.stock_code = target.stock_code
WHERE target.provider = 'kis'
  AND target.business_date = %s
  AND target.close_price IS NOT NULL
ORDER BY target.stock_code
