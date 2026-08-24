-- 기술지표·신호 계산의 원천이 되는 일봉. 추론 툴 `daily_history`와 브리핑, 신호 DAG가 쓴다.
--
-- **두 원천을 한 모양으로 읽는다.** 국내 지수는 `index_daily`(뷰 `quote_daily`)에, 국내 종목은
-- `stock_investor_trade_daily`에 있고 컬럼 이름이 다르다. 종목 확정 종가가 저기 있는 이유는
-- 수급과 같은 응답으로 오기 때문이다(`apps/models/market.py`의 StockInvestorTradeDaily).
--
-- KIS equity 행을 뷰 쪽에서 빼는 이유는 겹침을 막기 위해서다. 해외 상장 종목(TSMC ADR)은
-- yahoo provider라 그대로 통과한다.
--
-- **cutoff는 `created_at`이다.** 일봉은 마감 뒤에 들어오므로 `business_date`로만 걸면 장전
-- 슬롯이 아직 모르는 당일 봉을 본 것으로 읽는다.
--
-- `include_watched`가 참이면 국내 watched 종목을 요청 목록에 더한다. 그래서 브리핑과 신호
-- DAG는 종목이 늘어도 코드를 바꾸지 않는다.
--
-- 주석에 퍼센트 기호를 쓰지 않는다. psycopg가 주석까지 훑어 플레이스홀더로 센다.
WITH requested AS (
    SELECT unnest(%(symbols)s::text[]) AS symbol

    UNION

    SELECT ticker
    FROM instrument
    WHERE %(include_watched)s
      AND is_watched
      AND market IN ('kospi', 'kosdaq')
), normalized AS (
    SELECT daily.provider,
           daily.symbol,
           symbol.label,
           symbol.kind,
           symbol.country,
           daily.business_date,
           daily.open,
           daily.high,
           daily.low,
           daily.close,
           daily.volume,
           daily.created_at
    FROM quote_daily AS daily
    JOIN quote_symbol AS symbol
      ON symbol.provider = daily.provider
     AND symbol.symbol = daily.symbol
    JOIN requested ON requested.symbol = daily.symbol
    WHERE NOT (daily.provider = 'kis' AND symbol.kind = 'equity')

    UNION ALL

    SELECT daily.provider,
           daily.stock_code AS symbol,
           symbol.label,
           symbol.kind,
           symbol.country,
           daily.business_date,
           daily.open_price AS open,
           daily.high_price AS high,
           daily.low_price AS low,
           daily.close_price AS close,
           daily.accumulated_volume AS volume,
           daily.created_at
    FROM stock_investor_trade_daily AS daily
    JOIN quote_symbol AS symbol
      ON symbol.provider = daily.provider
     AND symbol.symbol = daily.stock_code
    JOIN requested ON requested.symbol = daily.stock_code
    WHERE daily.provider = 'kis'
), ranked AS (
    SELECT normalized.*,
           row_number() OVER (
               PARTITION BY provider, symbol
               ORDER BY business_date DESC
           ) AS position
    FROM normalized
    WHERE created_at <= %(as_of_at)s
)
SELECT provider,
       symbol,
       label,
       kind,
       country,
       business_date,
       open,
       high,
       low,
       close,
       volume
FROM ranked
WHERE position <= %(limit)s
ORDER BY symbol, business_date DESC
