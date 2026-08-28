-- 국내 종목의 투자자별 **일별 순매수 수량**. 사건 전 구간부터 반응 끝까지.
--
-- **`수급`·`외국인 수급` 채널을 숫자로 확인하는 자리다.** 이 툴이 없던 동안 모델은 종가
-- 셋(주간·T+1·T+5)만 보고 "외국인이 샀다"를 추론했다. 실제로 2026-08-12에 외국인이
-- 삼성전자를 +5,802,466주 순매수했고 종가가 255,500에서 274,500으로 올랐다.
--
-- 다섯만 준다. `stock_investor_trade_daily`는 투자자 구분을 열넷까지 갖지만 은행·종금·
-- 기타법인처럼 규모가 작은 칸까지 실으면 프롬프트만 무거워진다. 연기금과 투신은 기관
-- 안에서 방향이 자주 갈려 따로 둔다.
--
-- **수량이고 금액이 아니다.** 금액 칸(`*_net_buy_amount`)은 셋만 있어 나머지와 짝이
-- 안 맞는다. 한 단위로 통일하는 편이 모델이 크기를 비교하기 쉽다.
--
-- `as_of_at` cutoff를 걸지 않는 이유는 `price_window_*.sql`과 같다.
SELECT business_date,
       close_price,
       foreign_net_buy_qty,
       institution_net_buy_qty,
       individual_net_buy_qty,
       pension_fund_net_buy_qty,
       investment_trust_net_buy_qty
FROM stock_investor_trade_daily
WHERE stock_code = %(code)s
  AND business_date BETWEEN %(start)s AND %(end)s
ORDER BY business_date
