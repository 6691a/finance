-- 두 시계열의 일별 변화 상관과 **표본 수**.
--
-- 상관계수만 돌려주지 않는다. 24일로 낸 0.9와 2,500일로 낸 0.2는 다른 이야기이고, 표본 수가
-- 없으면 모델이 그걸 구분할 방법이 없다.
--
-- **금리는 수익률이 아니라 변화폭으로 본다.** 국채 금리에 로그 수익률을 씌우면 4.0에서
-- 4.1로 오른 것과 0.4에서 0.5로 오른 것이 전혀 다른 크기가 된다. 게다가 유로 지역은
-- 마이너스 금리 구간이 있어 로그를 취할 수 없다. kind가 rate면 차분, 아니면 로그 수익률이다.
--
-- 주석에 퍼센트 기호를 쓰지 않는다. psycopg2는 파일 전체에서 자리표시자를 찾으므로
-- 주석 안의 맨 기호 하나가 `IndexError: tuple index out of range`가 된다.
WITH windowed AS (
    SELECT
        provider,
        series_id,
        kind,
        business_date,
        value,
        lag(value) OVER (PARTITION BY provider, series_id ORDER BY business_date) AS previous
    FROM daily_series
    WHERE (provider = %s AND series_id = %s) OR (provider = %s AND series_id = %s)
),
changes AS (
    SELECT
        provider,
        series_id,
        business_date,
        CASE
            WHEN kind = 'rate' THEN value - previous
            WHEN previous > 0 AND value > 0 THEN ln(value / previous)
        END AS change
    FROM windowed
    WHERE previous IS NOT NULL
      AND (%s::date IS NULL OR business_date <= %s)
),
paired AS (
    SELECT l.business_date, l.change AS left_change, r.change AS right_change
    FROM changes l
    JOIN changes r ON r.business_date = l.business_date
    WHERE l.provider = %s AND l.series_id = %s
      AND r.provider = %s AND r.series_id = %s
      AND l.change IS NOT NULL AND r.change IS NOT NULL
    ORDER BY l.business_date DESC
    LIMIT %s
)
SELECT
    count(*) AS observations,
    min(business_date) AS first_date,
    max(business_date) AS last_date,
    corr(left_change, right_change) AS correlation
FROM paired
