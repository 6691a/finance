-- 종목의 한 세션 등락률. 추론의 관측 상태와 채점이 같은 원본을 읽는다.
--
-- **분봉(`stock_bar`)을 쓰지 않는다.** `is_final`은 REST 응답이라는 뜻이지 세션 완결이
-- 아니다 — 2026-08-13 005930은 15:19가 마지막 봉이고 마감 동시호가가 빠져 있었다
-- (`collectors/kis.py` `fetch_stock_bars` docstring 실측). 여기 `close_price`는
-- `kis_investor_trade_daily`가 18:10에 넣는 확정 종가(stck_clpr)다.
--
-- 전일 종가는 이 테이블에 컬럼으로 없어 LATERAL로 직전 거래일 행을 붙인다
-- (`select_latest.sql`과 같은 방법). 상장 첫날처럼 직전이 없으면 등락률이 NULL이고,
-- 부르는 쪽은 그 종목을 미채점으로 남긴다 — 0으로 꾸미지 않는다.
SELECT daily.stock_code,
       daily.business_date,
       daily.close_price,
       previous.close_price AS previous_close,
       CASE
           WHEN previous.close_price IS NULL OR previous.close_price = 0 THEN NULL
           ELSE (daily.close_price - previous.close_price) / previous.close_price * 100
       END AS return_pct
FROM stock_investor_trade_daily AS daily
LEFT JOIN LATERAL (
    SELECT prev.close_price
    FROM stock_investor_trade_daily AS prev
    WHERE prev.provider = daily.provider
      AND prev.stock_code = daily.stock_code
      AND prev.business_date < daily.business_date
    ORDER BY prev.business_date DESC
    LIMIT 1
) AS previous ON TRUE
WHERE daily.provider = 'kis'
  AND daily.business_date = %s
  AND daily.stock_code = ANY(%s)
ORDER BY daily.stock_code
