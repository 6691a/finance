-- 관측 상태의 일봉 창. 코스피 확정 일봉 최근 N행.
--
-- **영업일 달력을 세지 않는다.** 저장된 행 N개를 그대로 쓴다 — 휴장·수집 실패로 구멍이 나면
-- 그것이 창을 넓힐 뿐이고, 달력을 세면 없는 날을 기다리다 관측 상태가 서지 않는다.
--
-- **cutoff는 `created_at`이다.** 일봉은 마감 뒤에 들어오므로 `business_date`로만 걸면
-- 장전 슬롯이 아직 모르는 당일 봉을 본 것으로 읽는다.
--
-- `business_date < run_date`는 오늘 봉을 빼는 조건이다. 장후 관찰은 오늘을 포함해야 하므로
-- 그쪽이 `run_date + 1`을 넘긴다.
--
-- 등락률은 직전 행 대비다. 창 밖의 첫 행은 앞이 없어 NULL이고, 그건 "0"이 아니라
-- "재지 않았다"는 뜻이라 읽는 쪽이 그대로 둔다.
WITH recent AS (
    SELECT business_date,
           open,
           close,
           lag(close) OVER (ORDER BY business_date) AS previous_close
    FROM (
        SELECT business_date, open, close
        FROM index_daily
        WHERE provider = %(provider)s
          AND symbol = %(symbol)s
          AND business_date < %(before_date)s
          AND created_at <= %(as_of_at)s
        ORDER BY business_date DESC
        LIMIT %(limit)s
    ) AS window_rows
)
SELECT business_date,
       open,
       close,
       CASE
           WHEN previous_close IS NULL OR previous_close = 0 THEN NULL
           ELSE round((close - previous_close) / previous_close * 100, 2)
       END AS change_pct
FROM recent
ORDER BY business_date
