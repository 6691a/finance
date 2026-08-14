-- 종목별 외국인·기관 추정 순매수 한 슬롯을 저장한다.
-- 멱등 키는 (provider, stock_code, business_date, source_time_code)다.
--
-- **슬롯이 키에 들어간다.** 한 번 조회에 갱신 슬롯마다 한 행이 오므로, 수집 시각을 키로
-- 쓰면 그 행들이 같은 분에 몰려 마지막 하나만 남는다.
--
-- 정의의 원본은 `apps/models/market.py`의 `StockInvestorEstimateSnapshot`이고
-- `tests/collectors/test_kis_investor_flow.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO stock_investor_estimate_snapshot (
    provider, stock_code, business_date, source_time_code,
    foreign_net_buy_qty, institution_net_buy_qty, total_net_buy_qty,
    collected_at, source_record_id
) VALUES ('kis', %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, stock_code, business_date, source_time_code) DO UPDATE SET
    foreign_net_buy_qty = EXCLUDED.foreign_net_buy_qty,
    institution_net_buy_qty = EXCLUDED.institution_net_buy_qty,
    total_net_buy_qty = EXCLUDED.total_net_buy_qty,
    collected_at = EXCLUDED.collected_at,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
