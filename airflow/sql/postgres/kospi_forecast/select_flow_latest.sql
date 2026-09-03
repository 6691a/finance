-- 장중 슬롯의 오늘 수급. 기준 시각 이전 마지막 스냅샷 하나.
--
-- **관측 상태이지 툴이 아니다.** 장중 슬롯의 핵심 입력이라 매번 툴로 부르게 두면 호출
-- 상한만 먹는다. 요인별 이력이 필요하면 모델이 `factor_history`를 따로 부른다.
--
-- 수량이 값이고 금액은 원문 그대로다 — `*_net_buy_amount`의 단위가 확정돼 있지 않다.
--
-- 하한(개장 시각)을 받는 이유는 전일 마지막 스냅샷이 딸려 오는 것을 막기 위해서다.
SELECT observed_at,
       foreign_net_buy_qty,
       institution_net_buy_qty,
       individual_net_buy_qty
FROM market_investor_flow_snapshot
WHERE market_code = %(market_code)s
  AND observed_at <= %(as_of_at)s
  AND observed_at >= %(session_start)s
ORDER BY observed_at DESC
LIMIT 1
