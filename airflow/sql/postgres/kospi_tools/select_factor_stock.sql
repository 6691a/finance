-- `factor_history` 툴이 개별 종목(삼성전자·SK하이닉스)을 읽는다.
--
-- **국내 종목 확정 일봉은 `stock_daily`가 아니라 여기에 있다.** KIS가 종가와 수급을 같은
-- 응답으로 주기 때문이다(`apps/models/market/investor_flow.py`).
--
-- 종가 등락률과 수급을 함께 준다 — 이 요인을 묻는 이유가 "반도체 대형주가 어디로 갔나"라
-- 가격만으로는 답이 반쪽이다.
--
-- 순매수 대금 단위는 백만원이다(모델 주석). 시장 단위 스냅샷과 달리 여기는 확정돼 있다.
--
-- cutoff는 `created_at`이다. 18:10에 들어오는 확정 행이라 장중 슬롯은 오늘 행을 못 본다.
WITH recent AS (
    SELECT business_date,
           close_price,
           foreign_net_buy_qty,
           institution_net_buy_qty,
           individual_net_buy_qty
    FROM stock_investor_trade_daily
    WHERE provider = %(provider)s
      AND stock_code = %(stock_code)s
      AND created_at <= %(as_of_at)s
    ORDER BY business_date DESC
    LIMIT %(limit)s
)
SELECT business_date,
       close_price AS value,
       close_price - lag(close_price) OVER (ORDER BY business_date) AS change,
       CASE
           WHEN lag(close_price) OVER (ORDER BY business_date) IS NULL
             OR lag(close_price) OVER (ORDER BY business_date) = 0 THEN NULL
           ELSE round(
               (close_price - lag(close_price) OVER (ORDER BY business_date))
               / lag(close_price) OVER (ORDER BY business_date) * 100,
               2
           )
       END AS change_pct,
       foreign_net_buy_qty,
       institution_net_buy_qty,
       individual_net_buy_qty
FROM recent
ORDER BY business_date
