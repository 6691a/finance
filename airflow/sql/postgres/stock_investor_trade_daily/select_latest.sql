-- 종목마다 마지막 확정 거래일 하나. 장 마감 뒤 리포트가 읽는다.
-- 등락과 수급 비교는 직전 거래일 행과 한다. quote_bar와 달리 이 테이블에는 previous_close가
-- 없어 LATERAL로 직전 행을 통째로 붙이고, 종가뿐 아니라 그날 날짜와 수급 열두 칸도 함께 받는다.
-- 수집이 매일 도는 것이 아니라 직전 행이 전일이 아닐 수 있어 날짜를 함께 준다.
-- 상장 첫날처럼 직전이 없으면 NULL이고 렌더링이 '-'로 그린다.
-- 종목명은 instrument 마스터에서 가져온다. 마스터에 없으면 종목코드를 그대로 쓴다.
SELECT DISTINCT ON (daily.stock_code)
       daily.stock_code,
       COALESCE(instrument.name, daily.stock_code) AS label,
       daily.business_date,
       daily.close_price,
       previous.close_price AS previous_close,
       previous.business_date AS previous_business_date,
       previous.foreign_net_buy_qty AS previous_foreign_net_buy_qty,
       previous.institution_net_buy_qty AS previous_institution_net_buy_qty,
       previous.individual_net_buy_qty AS previous_individual_net_buy_qty,
       daily.foreign_net_buy_qty,
       daily.institution_net_buy_qty,
       daily.individual_net_buy_qty,
       daily.securities_net_buy_qty,
       daily.investment_trust_net_buy_qty,
       daily.private_equity_net_buy_qty,
       daily.bank_net_buy_qty,
       daily.insurance_net_buy_qty,
       daily.merchant_bank_net_buy_qty,
       daily.pension_fund_net_buy_qty,
       -- 기관계 밖의 둘. 셋을 더해야 개인·외국인과 합이 0으로 닫힌다.
       daily.other_corporation_net_buy_qty,
       daily.other_organization_net_buy_qty,
       -- 기관 세부·기타도 직전 거래일과 나란히 그린다. 값 하나만으로는 그날 방향이
       -- 이어진 것인지 뒤집힌 것인지 읽히지 않는다.
       previous.securities_net_buy_qty AS previous_securities_net_buy_qty,
       previous.investment_trust_net_buy_qty AS previous_investment_trust_net_buy_qty,
       previous.private_equity_net_buy_qty AS previous_private_equity_net_buy_qty,
       previous.bank_net_buy_qty AS previous_bank_net_buy_qty,
       previous.insurance_net_buy_qty AS previous_insurance_net_buy_qty,
       previous.merchant_bank_net_buy_qty AS previous_merchant_bank_net_buy_qty,
       previous.pension_fund_net_buy_qty AS previous_pension_fund_net_buy_qty,
       previous.other_corporation_net_buy_qty AS previous_other_corporation_net_buy_qty,
       previous.other_organization_net_buy_qty AS previous_other_organization_net_buy_qty
FROM stock_investor_trade_daily AS daily
LEFT JOIN instrument
  ON instrument.ticker = daily.stock_code
LEFT JOIN LATERAL (
    SELECT *
    FROM stock_investor_trade_daily AS prev
    WHERE prev.stock_code = daily.stock_code
      AND prev.business_date < daily.business_date
    ORDER BY prev.business_date DESC
    LIMIT 1
) AS previous ON TRUE
WHERE daily.business_date >= %s
ORDER BY daily.stock_code, daily.business_date DESC
