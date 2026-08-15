-- 여러 시계열의 구간 시작·끝 값과 변화폭. 계열마다 한 줄이다.
--
-- **이 툴이 없으면 분석가가 값을 나열하고 눈으로 비교한다.** 실측(grok-4, 2026-08-15)에서
-- 금리 분석가가 6개국 전 만기의 시작값과 끝값을 관찰에 그대로 옮겨 적고 "높아졌다"로
-- 끝냈다. 뺄셈을 할 도구가 없었기 때문이다. 계산은 SQL이 한다는 설계가 도구가 없으면
-- 지켜지지 않는다.
--
-- 관측 수와 실제 시작·끝 날짜를 함께 준다. 요청한 구간과 실제 구간이 다를 수 있고
-- (제공처 발표 시차) 나라를 견줄 때 그 차이가 결론을 바꾼다.
WITH bounded AS (
    SELECT provider, series_id, kind, business_date, value
    FROM daily_series
    WHERE (provider, series_id) IN (SELECT * FROM unnest(%s::text[], %s::text[]))
      AND (%s::date IS NULL OR business_date >= %s)
      AND (%s::date IS NULL OR business_date <= %s)
),
edges AS (
    SELECT
        provider,
        series_id,
        kind,
        min(business_date) AS first_date,
        max(business_date) AS last_date,
        count(*) AS observations
    FROM bounded
    GROUP BY provider, series_id, kind
)
SELECT
    edges.provider,
    edges.series_id,
    edges.kind,
    edges.first_date,
    edges.last_date,
    edges.observations,
    opening.value AS first_value,
    closing.value AS last_value
FROM edges
JOIN bounded AS opening
  ON opening.provider = edges.provider
 AND opening.series_id = edges.series_id
 AND opening.business_date = edges.first_date
JOIN bounded AS closing
  ON closing.provider = edges.provider
 AND closing.series_id = edges.series_id
 AND closing.business_date = edges.last_date
ORDER BY edges.provider, edges.series_id
