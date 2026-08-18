-- 시장마다 최근 며칠의 일별 외국인 순매수 금액. "며칠째 순매도인가"를 답하는 근거다.
--
-- 장중 스냅샷이 5분마다 쌓이므로 그날의 마지막 스냅샷을 그날의 값으로 삼는다. 날짜 경계는
-- KST다. 국내 시장 하나만 다루는 값이라 시장별 시간대를 따질 필요가 없다.
--
-- 여기서는 금액을 원 단위 그대로 준다. 억 단위 환산은 렌더링이 한다.
SELECT DISTINCT ON (market_code, (observed_at AT TIME ZONE 'Asia/Seoul')::date)
       market_code,
       (observed_at AT TIME ZONE 'Asia/Seoul')::date AS on_date,
       foreign_net_buy_amount
FROM market_investor_flow_snapshot
WHERE observed_at >= %s
ORDER BY market_code, (observed_at AT TIME ZONE 'Asia/Seoul')::date, observed_at DESC
