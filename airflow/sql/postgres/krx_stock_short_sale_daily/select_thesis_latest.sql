-- 추론 툴 `short_and_credit`가 쓴다. 추적 종목마다 최신 확정 영업일의 공매도와
-- 같은 날 대차 잔고, 그리고 신용융자 잔고.
--
-- 셋을 한 표에 두는 이유는 서로가 서로의 재고이기 때문이다. 공매도의 재고가 대차 잔고이고,
-- 신용융자는 반대편(빚내서 산 물량)이다. 따로 주면 모델이 셋을 잇지 못한다.
--
-- **당일 행을 반드시 뺀다.** KIS가 장중에 당일 공매도를 0으로 보낸다. 2026-08-21 실측:
-- `business_date = 2026-08-21`, `short_sale_quantity = 0`인 행이 그날 08:10 KST에 들어와
-- 있었다. `created_at` cutoff만으로는 안 걸러지므로(전날 밤에 들어온 행이다) 날짜로
-- 직접 자른다. 안 자르면 모델이 "오늘 공매도 0주"라는 거짓을 관측으로 읽는다.
-- 확정은 다음 영업일 갱신이 채운다. 브리핑의 `select_latest_with_lending.sql`도 같은 이유로
-- 당일을 뺀다.
--
-- 기준 날짜는 `as_of_at`의 **KST 날짜**다. UTC 날짜로 자르면 장전 슬롯(전날 23:35 UTC)에서
-- 하루가 밀린다.
--
-- 대차와 신용 행이 아직 없어도 공매도는 그려야 하므로 LEFT JOIN이다. 신용은 `trade_date`가
-- 날짜 컬럼이라 이름만 다르고 뜻은 같다.
--
-- 주석에 퍼센트 기호를 쓰지 않는다. psycopg가 주석까지 훑어 플레이스홀더로 센다.
SELECT DISTINCT ON (short_sale.stock_code)
       short_sale.stock_code,
       COALESCE(instrument.name, short_sale.stock_code) AS label,
       short_sale.business_date,
       short_sale.short_sale_quantity,
       short_sale.short_sale_volume_ratio,
       short_sale.short_sale_amount,
       lending.balance_quantity AS lending_balance_quantity,
       lending.balance_change_quantity AS lending_balance_change_quantity,
       credit.loan_balance_quantity AS credit_loan_balance_quantity,
       credit.loan_balance_amount AS credit_loan_balance_amount,
       credit.loan_balance_rate AS credit_loan_balance_rate
FROM krx_stock_short_sale_daily AS short_sale
LEFT JOIN krx_stock_securities_lending_daily AS lending
       ON lending.provider = short_sale.provider
      AND lending.stock_code = short_sale.stock_code
      AND lending.business_date = short_sale.business_date
      AND lending.created_at <= %(as_of_at)s
LEFT JOIN krx_stock_credit_balance_daily AS credit
       ON credit.provider = short_sale.provider
      AND credit.stock_code = short_sale.stock_code
      AND credit.trade_date = short_sale.business_date
      AND credit.created_at <= %(as_of_at)s
LEFT JOIN instrument
       ON instrument.ticker = short_sale.stock_code
WHERE short_sale.stock_code = ANY(%(stock_codes)s)
  AND short_sale.created_at <= %(as_of_at)s
  AND short_sale.business_date < (%(as_of_at)s AT TIME ZONE 'Asia/Seoul')::date
ORDER BY short_sale.stock_code, short_sale.business_date DESC
