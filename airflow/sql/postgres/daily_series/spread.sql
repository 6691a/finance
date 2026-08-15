-- 여러 쌍의 차이를 날짜별로. 곡선 기울기(10년-2년)와 나라 사이 벌어짐(한국-미국)이 이 모양이다.
--
-- **쌍마다 따로 부르면 조사 예산이 또 나열로 나간다.** 실측(grok-4, 2026-08-15)에서 금리
-- 분석가가 곡선·나라 스프레드를 하나씩 여덟 번 불러 호출 상한 12회를 다시 소진했다.
-- `get_series`를 묶은 것과 같은 이유로 여기도 묶는다.
--
-- 같은 날짜에 양쪽 다 값이 있는 날만 남긴다. 한쪽만 있는 날을 끼우면 차이가 발표 시차 때문에
-- 벌어진 것인지 시장이 움직인 것인지 알 수 없다.
--
-- **`compare_series`와 다르다.** 저쪽은 함께 움직이는 정도(상관)를 내고 여기는 벌어진 폭을
-- 낸다. 금리 곡선은 상관이 아니라 폭으로 읽는다.
WITH wanted AS (
    SELECT *
    FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[])
        AS pair(left_provider, left_series, right_provider, right_series)
),
joined AS (
    SELECT
        wanted.left_provider,
        wanted.left_series,
        wanted.right_provider,
        wanted.right_series,
        lhs.business_date,
        lhs.value AS left_value,
        rhs.value AS right_value,
        lhs.value - rhs.value AS spread
    FROM wanted
    JOIN daily_series AS lhs
      ON lhs.provider = wanted.left_provider AND lhs.series_id = wanted.left_series
    JOIN daily_series AS rhs
      ON rhs.provider = wanted.right_provider
     AND rhs.series_id = wanted.right_series
     AND rhs.business_date = lhs.business_date
    WHERE (%s::date IS NULL OR lhs.business_date >= %s)
      AND (%s::date IS NULL OR lhs.business_date <= %s)
),
ranked AS (
    SELECT
        joined.*,
        row_number() OVER (
            PARTITION BY left_provider, left_series, right_provider, right_series
            ORDER BY business_date DESC
        ) AS position
    FROM joined
)
SELECT left_provider, left_series, right_provider, right_series, business_date, left_value, right_value, spread
FROM ranked
WHERE position <= %s
ORDER BY left_provider, left_series, right_provider, right_series, business_date DESC
