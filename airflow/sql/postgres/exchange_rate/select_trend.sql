-- 통화마다 최근 며칠의 일별 매매기준율. 하루에 회차가 여러 번이라 그날의 마지막 회차를
-- 그날의 값으로 삼는다. 회차를 그대로 늘어놓으면 하루 안의 흔들림이 일별 변화로 잘못 잡힌다.
SELECT DISTINCT ON (currency, date)
       currency,
       date,
       exchange_standard_rate
FROM exchange_rate
WHERE currency = ANY(%s)
  AND date >= %s
ORDER BY currency, date, round DESC
