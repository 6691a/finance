-- 종목별 공매도 하루치를 저장한다. 멱등 키는 (provider, stock_code, business_date)다.
--
-- 당일 행이 와도 확정치가 아니다. 다음 영업일 아침 재조회가 같은 자연키를 갱신한다.
INSERT INTO krx_stock_short_sale_daily (
    provider, stock_code, business_date, close_price, accumulated_volume,
    short_sale_quantity, short_sale_volume_ratio,
    accumulated_short_sale_quantity, accumulated_short_sale_volume_ratio,
    short_sale_amount, short_sale_amount_ratio,
    accumulated_short_sale_amount, accumulated_short_sale_amount_ratio,
    total_amount, short_sale_average_price,
    source_record_id
) VALUES ('kis', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, stock_code, business_date) DO UPDATE SET
    close_price = EXCLUDED.close_price,
    accumulated_volume = EXCLUDED.accumulated_volume,
    short_sale_quantity = EXCLUDED.short_sale_quantity,
    short_sale_volume_ratio = EXCLUDED.short_sale_volume_ratio,
    accumulated_short_sale_quantity = EXCLUDED.accumulated_short_sale_quantity,
    accumulated_short_sale_volume_ratio = EXCLUDED.accumulated_short_sale_volume_ratio,
    short_sale_amount = EXCLUDED.short_sale_amount,
    short_sale_amount_ratio = EXCLUDED.short_sale_amount_ratio,
    accumulated_short_sale_amount = EXCLUDED.accumulated_short_sale_amount,
    accumulated_short_sale_amount_ratio = EXCLUDED.accumulated_short_sale_amount_ratio,
    total_amount = EXCLUDED.total_amount,
    short_sale_average_price = EXCLUDED.short_sale_average_price,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
