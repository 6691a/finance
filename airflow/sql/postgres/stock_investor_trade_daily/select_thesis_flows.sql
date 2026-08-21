-- 추론 툴 `stock_investor_flows`가 쓴다. 추적 종목의 최근 확정 수급 며칠치.
--
-- **확정값만 준다.** 이 테이블은 `kis_investor_trade_daily`가 KST 18:10에 채우는 마감 뒤
-- 값이라, 장전 슬롯(08:35)에서 보면 전 영업일까지가 마지막이다. 장중 추정치는 별도
-- 테이블(`stock_investor_estimate_snapshot`)이고 같은 툴이 따로 표시해 준다 — 확정과
-- 추정을 구분 없이 한 표에 섞으면 모델이 어느 쪽인지 모른 채 읽는다.
--
-- **cutoff는 `created_at`이다.** `business_date`로만 걸면 18:10에 들어온 당일 행을
-- 15:30 슬롯이 이미 본 것처럼 읽는다.
--
-- **종목마다 `days`행씩** 준다. 전체에 LIMIT을 걸면 한 종목이 그 자리를 다 먹는다.
WITH ranked AS (
    SELECT stock_code,
           business_date,
           close_price,
           accumulated_volume,
           foreign_net_buy_qty,
           institution_net_buy_qty,
           individual_net_buy_qty,
           foreign_net_buy_amount,
           institution_net_buy_amount,
           individual_net_buy_amount,
           ROW_NUMBER() OVER (
               PARTITION BY stock_code
               ORDER BY business_date DESC
           ) AS recency
    FROM stock_investor_trade_daily
    WHERE provider = 'kis'
      AND stock_code = ANY(%(stock_codes)s)
      AND created_at <= %(as_of_at)s
)
SELECT stock_code,
       business_date,
       close_price,
       accumulated_volume,
       foreign_net_buy_qty,
       institution_net_buy_qty,
       individual_net_buy_qty,
       foreign_net_buy_amount,
       institution_net_buy_amount,
       individual_net_buy_amount
FROM ranked
WHERE recency <= %(days)s
ORDER BY stock_code, business_date DESC
