-- 계열마다 최근 구간의 일별 값. 오늘 bp 변화가 평소만한지 재는 데 쓴다.
-- kind와 maturity_months로 좁히는 것은 select_latest_pair.sql과 같은 이유다.
-- 안 걸면 단위가 다른 계열이 한 분포에 섞여 백분위가 뜻을 잃는다.
SELECT observation.provider,
       observation.series_id,
       observation.observation_date,
       observation.value
FROM indicator_observation AS observation
JOIN indicator_series AS series
  ON series.provider = observation.provider
 AND series.series_id = observation.series_id
WHERE series.kind = %s
  AND series.maturity_months = %s
  AND observation.observation_date >= %s
ORDER BY observation.provider, observation.series_id, observation.observation_date
