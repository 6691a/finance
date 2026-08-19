-- 금리 스프레드 계산용. 계열마다 최신 관측값과 직전 관측값을 읽는다.
-- provider와 series_id를 각각 ANY로 거르므로 목록에 없는 조합이 섞일 수 있고,
-- 짝 맞추기와 스프레드 계산은 파이썬이 한다.
SELECT provider,
       series_id,
       observation_date,
       value,
       previous_value
FROM (
    SELECT provider,
           series_id,
           observation_date,
           value,
           LAG(value) OVER (PARTITION BY provider, series_id ORDER BY observation_date) AS previous_value,
           ROW_NUMBER() OVER (PARTITION BY provider, series_id ORDER BY observation_date DESC) AS recency
    FROM indicator_observation
    WHERE provider = ANY(%s)
      AND series_id = ANY(%s)
      AND observation_date >= %s
) AS ranked
WHERE recency = 1
