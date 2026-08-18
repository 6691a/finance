-- 종목마다 마지막 갱신 슬롯 하나. 값은 그 시점까지의 당일 누적이라 슬롯이 클수록 최신이다.
-- 슬롯 코드는 시각이 아니라 회차라 문자열 정렬로는 '10'이 '9'보다 앞에 온다. 숫자로 캐스팅해
-- 정렬한다. KIS가 숫자가 아닌 코드를 보내면 여기서 터진다 — 조용히 엉뚱한 슬롯을 최신으로
-- 고르는 것보다 낫다.
-- 종목명은 instrument 마스터에서 가져온다. 마스터에 없으면 종목코드를 그대로 쓴다.
SELECT DISTINCT ON (snapshot.stock_code)
       snapshot.stock_code,
       COALESCE(instrument.name, snapshot.stock_code) AS label,
       snapshot.business_date,
       snapshot.foreign_net_buy_qty,
       snapshot.institution_net_buy_qty,
       snapshot.total_net_buy_qty,
       snapshot.collected_at
FROM stock_investor_estimate_snapshot AS snapshot
LEFT JOIN instrument
  ON instrument.ticker = snapshot.stock_code
WHERE snapshot.business_date >= %s
ORDER BY snapshot.stock_code, snapshot.business_date DESC, snapshot.source_time_code::int DESC
