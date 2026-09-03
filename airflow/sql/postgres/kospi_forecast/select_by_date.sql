-- 그 날의 전망 전부(슬롯 순서). 셋이 쓴다.
--
-- ① 재실행이 LLM을 다시 부를지 판정한다(첫 성공본 불변).
-- ② 장중 슬롯의 관측 상태에 앞 슬롯 답을 싣는다.
-- ③ 장후 관찰과 Slack이 오늘 전부를 읽는다.
--
-- `slot_order`로 정렬하는 이유는 슬롯 값이 알파벳순(midday < pre_close < pre_open)이면
-- 시간 순서와 어긋나기 때문이다. 이름을 시각으로 짓지 않은 대가를 여기서 한 번 치른다.
SELECT id,
       run_date,
       slot,
       as_of_at,
       base_price,
       base_at,
       so_far_pct,
       direction,
       expected_change_pct,
       band_pct,
       reasons,
       weak,
       rejected_reasons,
       actual_change_pct,
       hit,
       within_band,
       graded_at,
       prompt_version,
       llm_model
FROM kospi_forecast
WHERE run_date = %(run_date)s
ORDER BY CASE slot
             WHEN 'pre_open' THEN 1
             WHEN 'midday' THEN 2
             WHEN 'pre_close' THEN 3
             ELSE 9
         END
