-- 계열마다 최신 관측값과 그 직전 값을 준다. 델타를 bp로 그리기 위해서다.
-- kind와 maturity_months로 좁히는 것이 이 쿼리의 핵심이다. 한 테이블에 물가지수와
-- 소매판매가 함께 있어 걸지 않으면 단위가 다른 값이 한 표에 섞인다.
-- 나라가 늘어도 마스터가 흡수하므로 이 쿼리는 고치지 않는다.
WITH ranked AS (
    SELECT observation.provider,
           observation.series_id,
           observation.observation_date,
           observation.value,
           series.country,
           series.country_name,
           series.label,
           ROW_NUMBER() OVER (
               PARTITION BY observation.provider, observation.series_id
               ORDER BY observation.observation_date DESC
           ) AS recency
    FROM indicator_observation AS observation
    JOIN indicator_series AS series
      ON series.provider = observation.provider
     AND series.series_id = observation.series_id
    WHERE series.kind = %s
      AND series.maturity_months = %s
      AND observation.observation_date >= %s
)
SELECT latest.provider,
       latest.series_id,
       latest.country,
       latest.country_name,
       latest.label,
       latest.observation_date,
       latest.value,
       previous.value
FROM ranked AS latest
LEFT JOIN ranked AS previous
       ON previous.provider = latest.provider
      AND previous.series_id = latest.series_id
      AND previous.recency = 2
WHERE latest.recency = 1
ORDER BY latest.country, latest.series_id
