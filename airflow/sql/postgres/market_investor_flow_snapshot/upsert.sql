-- 시장별 투자자 누적 매매동향 한 분을 저장한다.
-- 멱등 키는 (provider, market_code, observed_at)다.
--
-- **누적값이라 델타를 저장하지 않는다.** 5분 변화량은 조회에서 lag()로 계산한다.
-- 수량과 대금은 KIS 표기 그대로다. 배율이 미확정이라 환산하지 않는다.
--
-- 한 응답에 12개 투자자 분류가 온다. 상위 셋만 매도·매수·대금을 함께 담고 나머지 아홉은
-- 순매수 수량만 담는다.
INSERT INTO market_investor_flow_snapshot (
    provider, market_code, observed_at,
    foreign_sell_qty, foreign_buy_qty, foreign_net_buy_qty, foreign_net_buy_amount,
    institution_sell_qty, institution_buy_qty, institution_net_buy_qty, institution_net_buy_amount,
    individual_sell_qty, individual_buy_qty, individual_net_buy_qty, individual_net_buy_amount,
    -- 기관 세부와 기타 분류는 순매수 수량만 담는다. 방향이 필요한 값이고 대금은 배율이
    -- 미확정이라 지금 넣어도 읽을 수 없다.
    securities_net_buy_qty,
    investment_trust_net_buy_qty,
    private_equity_net_buy_qty,
    bank_net_buy_qty,
    insurance_net_buy_qty,
    merchant_bank_net_buy_qty,
    pension_fund_net_buy_qty,
    other_corporation_net_buy_qty,
    other_organization_net_buy_qty,
    source_record_id
) VALUES ('kis', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, market_code, observed_at) DO UPDATE SET
    foreign_sell_qty = EXCLUDED.foreign_sell_qty,
    foreign_buy_qty = EXCLUDED.foreign_buy_qty,
    foreign_net_buy_qty = EXCLUDED.foreign_net_buy_qty,
    foreign_net_buy_amount = EXCLUDED.foreign_net_buy_amount,
    institution_sell_qty = EXCLUDED.institution_sell_qty,
    institution_buy_qty = EXCLUDED.institution_buy_qty,
    institution_net_buy_qty = EXCLUDED.institution_net_buy_qty,
    institution_net_buy_amount = EXCLUDED.institution_net_buy_amount,
    individual_sell_qty = EXCLUDED.individual_sell_qty,
    individual_buy_qty = EXCLUDED.individual_buy_qty,
    individual_net_buy_qty = EXCLUDED.individual_net_buy_qty,
    individual_net_buy_amount = EXCLUDED.individual_net_buy_amount,
    securities_net_buy_qty = EXCLUDED.securities_net_buy_qty,
    investment_trust_net_buy_qty = EXCLUDED.investment_trust_net_buy_qty,
    private_equity_net_buy_qty = EXCLUDED.private_equity_net_buy_qty,
    bank_net_buy_qty = EXCLUDED.bank_net_buy_qty,
    insurance_net_buy_qty = EXCLUDED.insurance_net_buy_qty,
    merchant_bank_net_buy_qty = EXCLUDED.merchant_bank_net_buy_qty,
    pension_fund_net_buy_qty = EXCLUDED.pension_fund_net_buy_qty,
    other_corporation_net_buy_qty = EXCLUDED.other_corporation_net_buy_qty,
    other_organization_net_buy_qty = EXCLUDED.other_organization_net_buy_qty,
    source_record_id = EXCLUDED.source_record_id,
    updated_at = now()
