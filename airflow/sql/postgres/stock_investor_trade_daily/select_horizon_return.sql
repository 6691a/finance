-- 종목의 T+N 누적 등락률. 다지평 채점이 읽는다.
--
-- **기준가는 지평이 달라도 같다** — 예측일 전 영업일 종가다. 지평마다 기준가를 옮기면
-- (예: T+3을 T+2 종가 대비로) 누적이 연속되지 않아 T+1과 T+5를 비교할 수 없다.
--
-- **분봉(`stock_bar`)을 쓰지 않는다.** `is_final`은 REST 응답이라는 뜻이지 세션 완결이
-- 아니다 — 2026-08-13 005930은 15:19가 마지막 봉이고 마감 동시호가가 빠져 있었다
-- (`collectors/kis.py` `fetch_stock_bars` docstring 실측). 여기 `close_price`는
-- `kis_investor_trade_daily`가 18:10에 넣는 확정 종가(stck_clpr)다.
--
-- 파라미터는 (예측일, 목표 거래일, 종목코드 목록)이다. 목표 거래일은 파이썬이
-- `market_session/select_nth_open_day.sql`로 먼저 구해 넘긴다 — 영업일 세기를 SQL 두 곳에
-- 나눠 두면 한쪽만 고쳐지는 날이 온다.
--
-- 종가 행이 없으면(휴장·미수집·상장폐지) 그 종목은 결과에 없다. 부르는 쪽은 미채점으로
-- 남긴다. 0으로 꾸미지 않는다.
WITH bounds AS (
    SELECT %s::date AS run_date,
           %s::date AS target_date
)
SELECT target.stock_code,
       bounds.target_date,
       base.close_price AS base_close,
       target.close_price AS target_close,
       (target.close_price - base.close_price) / base.close_price * 100 AS return_pct
FROM stock_investor_trade_daily AS target
CROSS JOIN bounds
JOIN LATERAL (
    SELECT prev.close_price
    FROM stock_investor_trade_daily AS prev
    WHERE prev.provider = target.provider
      AND prev.stock_code = target.stock_code
      AND prev.business_date < bounds.run_date
    ORDER BY prev.business_date DESC
    LIMIT 1
) AS base ON base.close_price <> 0
WHERE target.provider = 'kis'
  AND target.business_date = bounds.target_date
  AND target.stock_code = ANY(%s)
ORDER BY target.stock_code
