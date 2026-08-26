-- 심볼마다 **모든 거래일**의 N거래일 뒤 누적 등락률. **무조건 기저다.**
--
-- 이것이 없으면 조건부 기저율이 거짓말을 한다. 신호 뒤 상승이 60퍼센트라도 그 심볼의 평소
-- 상승이 55퍼센트면 그 신호가 더하는 것은 5퍼센트포인트다. 조건부만 주면 모델이 60을 크게
-- 읽는다(docs/analysis/market-thesis/10-base-rate.md 5.2절).
--
-- `select_forward_returns.sql`과 봉을 읽는 방식은 같고 사건 조인만 없다. 두 파일로 나눠 둔
-- 이유는 조인이 다르면 한쪽에 필요한 파라미터가 다른 쪽에 얹히기 때문이다.
--
-- cutoff와 look-ahead 규칙은 그 파일과 같다 — `business_date`로 걸고, 지평까지 지난 봉만 센다.
--
-- 주석에 퍼센트 기호를 쓰지 않는다. psycopg가 주석까지 훑어 플레이스홀더로 센다.
WITH bars AS (
    SELECT daily.provider,
           daily.symbol,
           daily.business_date,
           daily.close,
           row_number() OVER (
               PARTITION BY daily.provider, daily.symbol
               ORDER BY daily.business_date
           ) AS position
    FROM quote_daily AS daily
    JOIN quote_symbol AS symbol
      ON symbol.provider = daily.provider
     AND symbol.symbol = daily.symbol
    WHERE daily.business_date <= %(as_of_date)s
      AND daily.close > 0
      AND NOT (daily.provider = 'kis' AND symbol.kind = 'equity')

    UNION ALL

    SELECT daily.provider,
           daily.stock_code AS symbol,
           daily.business_date,
           daily.close_price AS close,
           row_number() OVER (
               PARTITION BY daily.provider, daily.stock_code
               ORDER BY daily.business_date
           ) AS position
    FROM stock_investor_trade_daily AS daily
    WHERE daily.business_date <= %(as_of_date)s
      AND daily.close_price > 0
      AND daily.provider = 'kis'
), requested AS (
    SELECT unnest(%(symbols)s::text[]) AS symbol
), horizons AS (
    SELECT unnest(%(horizons)s::int[]) AS horizon_days
)
SELECT base.symbol,
       horizons.horizon_days,
       round((future.close - base.close) / base.close * 100, 4) AS return_pct
FROM bars AS base
JOIN requested ON requested.symbol = base.symbol
CROSS JOIN horizons
JOIN bars AS future
  ON future.provider = base.provider
 AND future.symbol = base.symbol
 AND future.position = base.position + horizons.horizon_days
WHERE future.business_date <= %(as_of_date)s
ORDER BY base.symbol, horizons.horizon_days, base.business_date
