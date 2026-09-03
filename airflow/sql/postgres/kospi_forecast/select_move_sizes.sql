-- 크기 기준선. 모델이 "얼마나 움직일까"를 부를 때 딛고 설 실측 분포다.
--
-- **왜 필요한가.** 프롬프트가 "최근 일봉의 진폭에서 출발한다"고만 말하면 모델은 창 안의
-- 열다섯 봉을 눈대중한다. 2026-09-03 실측에서 그 결과가 중앙값 이동 2.27퍼센트인 시장에
-- 폭 1.00퍼센트포인트였다 — 구조적으로 못 맞히는 값이다. 분포를 직접 준다.
--
-- **방향별로 나눈다.** 프롬프트가 요구하는 것이 조건부 크기("오른다면 얼마")라서다.
-- 이 시장은 상승일과 하락일의 크기가 다르다(상승 중앙값 2.11, 하락 중앙값 -3.25).
--
-- **표본이 모자라면 비운다.** 부르는 쪽이 NULL을 그대로 싣고 프롬프트가 "재지 않았다"로
-- 읽게 한다. 0으로 채우면 모델이 그 숫자를 쓴다.
--
-- cutoff는 `created_at`이다. 일봉은 마감 뒤에 들어오므로 `business_date`로만 걸면 장전
-- 슬롯이 아직 모르는 당일 봉을 본다.
WITH recent AS (
    SELECT business_date,
           close,
           lag(close) OVER (ORDER BY business_date) AS previous_close
    FROM (
        SELECT business_date, close
        FROM index_daily
        WHERE provider = %(provider)s
          AND symbol = %(symbol)s
          AND business_date < %(before_date)s
          AND created_at <= %(as_of_at)s
        ORDER BY business_date DESC
        LIMIT %(limit)s
    ) AS window_rows
), moves AS (
    SELECT (close - previous_close) / previous_close * 100 AS change_pct
    FROM recent
    WHERE previous_close IS NOT NULL AND previous_close <> 0
)
SELECT count(*) AS observations,
       round(percentile_cont(0.25) WITHIN GROUP (ORDER BY abs(change_pct))::numeric, 2) AS abs_p25,
       round(percentile_cont(0.50) WITHIN GROUP (ORDER BY abs(change_pct))::numeric, 2) AS abs_p50,
       round(percentile_cont(0.75) WITHIN GROUP (ORDER BY abs(change_pct))::numeric, 2) AS abs_p75,
       round(percentile_cont(0.90) WITHIN GROUP (ORDER BY abs(change_pct))::numeric, 2) AS abs_p90,
       round(percentile_cont(0.50) WITHIN GROUP (
           ORDER BY change_pct) FILTER (WHERE change_pct > 0)::numeric, 2) AS up_median,
       round(percentile_cont(0.50) WITHIN GROUP (
           ORDER BY change_pct) FILTER (WHERE change_pct < 0)::numeric, 2) AS down_median,
       count(*) FILTER (WHERE change_pct > 0) AS up_days
FROM moves
