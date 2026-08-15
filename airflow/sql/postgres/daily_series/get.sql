-- 여러 시계열의 일별 값을 한 번에. 계열마다 최근 것부터 limit개씩 준다.
--
-- **한 번에 하나만 받으면 조사 예산이 나열로 다 나간다.** 실측(grok-4, 2026-08-15)에서 금리
-- 분석가가 6개국을 한 계열씩 받다가 툴 호출 상한 12회를 그대로 소진했고, 정작 상관을 내는
-- `compare_series`는 한 번도 부르지 못했다. 계산하라고 만든 툴을 못 쓰면 툴을 둔 뜻이 없다.
--
-- 좌표는 (provider, series_id) 쌍이라 배열 둘을 나란히 받아 unnest로 짝짓는다. 자리표시자
-- 수가 계열 수에 따라 변하지 않아 문장이 고정된다.
WITH picked AS (
    SELECT
        provider,
        series_id,
        business_date,
        value,
        row_number() OVER (PARTITION BY provider, series_id ORDER BY business_date DESC) AS position
    FROM daily_series
    WHERE (provider, series_id) IN (SELECT * FROM unnest(%s::text[], %s::text[]))
      AND (%s::date IS NULL OR business_date >= %s)
      AND (%s::date IS NULL OR business_date <= %s)
)
SELECT provider, series_id, business_date, value
FROM picked
WHERE position <= %s
ORDER BY provider, series_id, business_date DESC
