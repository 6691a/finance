-- 종목별 최신 **확정** 영업일의 공매도와 같은 날 대차 잔고. 공매도의 재고가 대차 잔고라
-- 한 표에 그린다. 당일 행은 제외한다 — KIS가 장중에 당일 행을 0으로 보내므로(실측)
-- 포함하면 "오늘 공매도 0주"라는 거짓이 표에 실린다. 확정은 다음 영업일 갱신이 한다.
-- **날짜만으로는 부족하다.** 그 0인 행은 그날 아침 수집이 쓴 것이고 다음 날 아침 수집이
-- 실제 값으로 덮는다. 덮는 실행이 한 번이라도 빠지면 0인 행이 그대로 과거 날짜가 되어
-- 당일 필터를 통과하고, 표에 "공매도 0주 / 등락률 -100.00" 이 실린다(2026-08-31 실측 —
-- 토요일 run이 건너뛰어 08-28 행이 0으로 남았다). 그래서 갱신 시각의 KST 날짜가
-- business_date보다 뒤인 행, 곧 **다음 날 이후의 실행이 확인해 준 행만** 본다.
-- 대차 행이 아직 없어도 공매도는 그려야 하므로 LEFT JOIN이다.
-- 직전 값은 같은 조회 창 안의 직전 **수집일** 행이다(LAG). 수집이 매일 도는 것이 아니라
-- 전일이 아닐 수 있어 그 날짜(previous_business_date)를 함께 돌려주고 브리핑이 표에 적는다.
-- 창 안에 직전 행이 없으면 NULL이고 브리핑이 직전·등락률을 `-`로 그린다.
-- 대차도 KIS가 주는 balance_change_quantity가 아니라 직전 행의 잔고를 쓴다. 그래야 표의
-- 직전 값과 등락률이 같은 날짜를 가리킨다.
-- 종목명은 instrument 마스터에서 가져오고 없으면 종목코드를 그대로 쓴다. 조인이 아니라
-- 스칼라 서브쿼리다 — 같은 티커가 마스터에 둘 있으면 조인은 행을 늘려 LAG가 같은 날짜를
-- 전일로 집는다.
SELECT DISTINCT ON (stock_code)
       stock_code,
       label,
       business_date,
       previous_business_date,
       short_sale_quantity,
       previous_short_sale_quantity,
       short_sale_volume_ratio,
       balance_quantity,
       previous_balance_quantity
FROM (
    SELECT short_sale.stock_code,
           COALESCE(
               (SELECT instrument.name FROM instrument WHERE instrument.ticker = short_sale.stock_code LIMIT 1),
               short_sale.stock_code
           ) AS label,
           short_sale.business_date,
           LAG(short_sale.business_date) OVER (
               PARTITION BY short_sale.provider, short_sale.stock_code
               ORDER BY short_sale.business_date
           ) AS previous_business_date,
           short_sale.short_sale_quantity,
           LAG(short_sale.short_sale_quantity) OVER (
               PARTITION BY short_sale.provider, short_sale.stock_code
               ORDER BY short_sale.business_date
           ) AS previous_short_sale_quantity,
           short_sale.short_sale_volume_ratio,
           lending.balance_quantity,
           LAG(lending.balance_quantity) OVER (
               PARTITION BY short_sale.provider, short_sale.stock_code
               ORDER BY short_sale.business_date
           ) AS previous_balance_quantity
    FROM krx_stock_short_sale_daily AS short_sale
    LEFT JOIN krx_stock_securities_lending_daily AS lending
      ON lending.provider = short_sale.provider
     AND lending.stock_code = short_sale.stock_code
     AND lending.business_date = short_sale.business_date
     AND (lending.updated_at AT TIME ZONE 'Asia/Seoul')::date > lending.business_date
    WHERE short_sale.business_date >= %s
      AND short_sale.business_date < %s
      AND (short_sale.updated_at AT TIME ZONE 'Asia/Seoul')::date > short_sale.business_date
) AS with_previous
ORDER BY stock_code, business_date DESC
