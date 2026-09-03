-- 채점과 장후 관찰이 읽는 그날 확정 종가. `kis_index_daily`(18:20)가 넣는다.
--
-- **없으면 채점하지 않는다.** 0으로 꾸미지 않는다 — 휴장일과 수집 지연이 같아 보이면
-- 채점이 없는 날을 "맞혔다"로 센다.
--
-- 여기에는 `created_at` cutoff를 걸지 않는다. 채점은 **일부러 미래를 보는 값**이다.
SELECT business_date, close
FROM index_daily
WHERE provider = %(provider)s
  AND symbol = %(symbol)s
  AND business_date = %(business_date)s
