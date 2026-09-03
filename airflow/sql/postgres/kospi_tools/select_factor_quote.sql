-- `factor_history` 툴이 `quote_daily` 계열 요인을 읽는다(미국 지수·환율·금리·원자재).
--
-- **뷰를 읽는다.** `quote_daily`는 kind별 물리 테이블의 UNION ALL이라 심볼 하나가 어느
-- 테이블에 있는지 부르는 쪽이 몰라도 된다. 쓰기는 절대 여기로 가지 않는다.
--
-- 변화는 **직전 관측 대비**다. 창 기준이 아니다 — 계열마다 주기가 달라 창 기준으로 접으면
-- 며칠치 변화가 하루 변화로 읽힌다.
--
-- **금리 계열의 변화를 퍼센트로 주지 않는다.** bp 차이가 필요한 요인은 부르는 쪽
-- (`domain.FactorSpec.unit`)이 알고 여기서는 원값 차이를 준다. 4.65에서 4.70으로 간 것을
-- 퍼센트 변화로 읽는 실수를 막는 자리다. 주석에 퍼센트 기호를 쓰지 않는다 — psycopg가
-- 주석까지 훑어 플레이스홀더로 센다.
--
-- cutoff는 `created_at`이다. 마감 뒤에 들어오는 값이라 `business_date`로만 걸면 장전
-- 슬롯이 아직 모르는 당일 행을 본 것으로 읽는다.
WITH recent AS (
    SELECT business_date, close
    FROM quote_daily
    WHERE symbol = %(symbol)s
      AND created_at <= %(as_of_at)s
    ORDER BY business_date DESC
    LIMIT %(limit)s
)
SELECT business_date,
       close AS value,
       close - lag(close) OVER (ORDER BY business_date) AS change,
       CASE
           WHEN lag(close) OVER (ORDER BY business_date) IS NULL
             OR lag(close) OVER (ORDER BY business_date) = 0 THEN NULL
           ELSE round(
               (close - lag(close) OVER (ORDER BY business_date))
               / lag(close) OVER (ORDER BY business_date) * 100,
               2
           )
       END AS change_pct
FROM recent
ORDER BY business_date
