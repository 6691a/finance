-- `factor_history` 툴이 `indicator_observation` 계열 요인을 읽는다(국고채, 기준금리).
--
-- **`provider`를 함께 건다.** `series_id`는 제공처 안에서만 고유하다 — 하나로만 걸면
-- 제공처가 늘어날 때 조용히 틀린다(저장소 규칙).
--
-- 변화는 직전 관측 대비 원값 차이다. 금리 계열이라 부르는 쪽이 bp로 표기한다.
-- 기준금리처럼 며칠씩 같은 값이 이어지는 계열은 변화가 0인 행이 대부분이고, 그것이 정상이다.
WITH recent AS (
    SELECT observation_date, value
    FROM indicator_observation
    WHERE provider = %(provider)s
      AND series_id = %(series_id)s
      AND created_at <= %(as_of_at)s
    ORDER BY observation_date DESC
    LIMIT %(limit)s
)
SELECT observation_date AS business_date,
       value,
       value - lag(value) OVER (ORDER BY observation_date) AS change,
       NULL::numeric AS change_pct
FROM recent
ORDER BY observation_date
