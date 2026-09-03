-- 아직 채점하지 않은 전망. **날짜 상한이 없다** — 장후 DAG가 실패한 날의 전망도 다음 날
-- 실행이 회수한다.
--
-- 종가가 아직 없는 날은 부르는 쪽이 건너뛴다. 여기서 조인해 걸러 내지 않는 이유는
-- "종가가 없다"와 "채점 대상이 아니다"를 부르는 쪽이 갈라 로그에 남겨야 하기 때문이다.
SELECT id,
       run_date,
       slot,
       base_price,
       direction,
       expected_change_pct,
       band_pct
FROM kospi_forecast
WHERE graded_at IS NULL
  AND run_date <= %(run_date)s
ORDER BY run_date, CASE slot
                       WHEN 'pre_open' THEN 1
                       WHEN 'midday' THEN 2
                       WHEN 'pre_close' THEN 3
                       ELSE 9
                   END
LIMIT %(limit)s
