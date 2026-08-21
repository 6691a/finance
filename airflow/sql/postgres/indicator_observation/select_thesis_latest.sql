-- 추론 툴 `macro_indicators`가 쓴다. 계열마다 최신 관측값과 그 직전 값을 준다.
--
-- 브리핑의 `select_latest_pair.sql`과 목적은 같지만 **창의 끝이 다르다.** 브리핑은
-- 지금까지를 보고 추론은 `as_of_at`까지만 본다. 그래서 파일을 나눈다 — 브리핑 쿼리에
-- 상한을 얹으면 브리핑이 쓰지 않는 파라미터를 매번 넘겨야 한다.
--
-- **cutoff는 `observation_date`가 아니라 `created_at`이다.** 관측일은 제공처의 영업일이고,
-- 우리가 그 값을 언제 알았는지는 별개다. FRED가 어제치를 오늘 새벽에 고시하면 관측일로
-- 걸러 봐야 아침 슬롯이 그것을 이미 본 것처럼 읽는다.
--
-- kind로 좁히는 것이 이 쿼리의 두 번째 핵심이다. 한 테이블에 국채 금리와 물가지수와
-- 소매판매가 함께 있어 걸지 않으면 단위가 다른 값이 한 표에 섞인다.
-- 나라가 늘어도 마스터가 흡수하므로 이 쿼리는 고치지 않는다.
WITH ranked AS (
    SELECT observation.provider,
           observation.series_id,
           observation.observation_date,
           observation.value,
           observation.unit,
           series.country,
           series.country_name,
           series.label,
           series.kind,
           series.maturity_months,
           ROW_NUMBER() OVER (
               PARTITION BY observation.provider, observation.series_id
               ORDER BY observation.observation_date DESC
           ) AS recency
    FROM indicator_observation AS observation
    JOIN indicator_series AS series
      ON series.provider = observation.provider
     AND series.series_id = observation.series_id
    WHERE series.kind = ANY(%(kinds)s)
      AND observation.created_at <= %(as_of_at)s
)
SELECT latest.provider,
       latest.series_id,
       latest.country,
       latest.country_name,
       latest.label,
       latest.kind,
       latest.maturity_months,
       latest.unit,
       latest.observation_date,
       latest.value,
       previous.value AS previous_value,
       previous.observation_date AS previous_date
FROM ranked AS latest
LEFT JOIN ranked AS previous
       ON previous.provider = latest.provider
      AND previous.series_id = latest.series_id
      AND previous.recency = 2
WHERE latest.recency = 1
ORDER BY latest.kind, latest.country, latest.maturity_months NULLS FIRST, latest.series_id
LIMIT %(limit)s
