-- 종목별 최신 **확정** 영업일의 공매도와 같은 날 대차 잔고. 공매도의 재고가 대차 잔고라
-- 한 표에 그린다. 당일 행은 제외한다 — KIS가 장중에 당일 행을 0으로 보내므로(실측)
-- 포함하면 "오늘 공매도 0주"라는 거짓이 표에 실린다. 확정은 다음 영업일 갱신이 한다.
-- 대차 행이 아직 없어도 공매도는 그려야 하므로 LEFT JOIN이다.
-- 종목명은 instrument 마스터에서 가져오고 없으면 종목코드를 그대로 쓴다.
SELECT DISTINCT ON (short_sale.stock_code)
       short_sale.stock_code,
       COALESCE(instrument.name, short_sale.stock_code) AS label,
       short_sale.business_date,
       short_sale.short_sale_quantity,
       short_sale.short_sale_volume_ratio,
       lending.balance_quantity,
       lending.balance_change_quantity
FROM krx_stock_short_sale_daily AS short_sale
LEFT JOIN krx_stock_securities_lending_daily AS lending
  ON lending.provider = short_sale.provider
 AND lending.stock_code = short_sale.stock_code
 AND lending.business_date = short_sale.business_date
LEFT JOIN instrument
  ON instrument.ticker = short_sale.stock_code
WHERE short_sale.business_date >= %s
  AND short_sale.business_date < %s
ORDER BY short_sale.stock_code, short_sale.business_date DESC
