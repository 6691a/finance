-- 장전 슬롯의 기준가. 대상 날짜 **앞의** 마지막 확정 종가와 그 날짜.
--
-- 전일이 아니라 "앞의 마지막"이다. 연휴 뒤 첫 거래일은 사흘 전 종가가 기준가이고, 그것이
-- 맞는 축이다 — 그 사이에 KRX 정규장 거래가 없었다.
--
-- cutoff가 `created_at`인 이유는 `select_bars.sql`과 같다.
SELECT business_date, close
FROM index_daily
WHERE provider = %(provider)s
  AND symbol = %(symbol)s
  AND business_date < %(before_date)s
  AND created_at <= %(as_of_at)s
ORDER BY business_date DESC
LIMIT 1
