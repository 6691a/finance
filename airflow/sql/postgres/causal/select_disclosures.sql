-- 그 주 대상 종목의 DART 공시 전량.
--
-- 문서와 달리 상위 몇 건으로 안 좁힌다. 한 주에 종목당 열 건 안팎이라 전부 실어도 되고,
-- 공시는 사건 그 자체라 점수로 거를 근거가 없다.
--
-- 대상 종류 중 **종목만** 공시를 갖는다. 지수·환율·금리는 여기 안 걸린다.
--
-- `as_of_at` cutoff를 건다. 근거는 "그 시점에 알 수 있었던 것"이어야 한다.
SELECT stock_code,
       rcept_no,
       company_name,
       report_name,
       receipt_date
FROM disclosure_event
WHERE stock_code = ANY(%(codes)s)
  AND receipt_date BETWEEN %(week_start)s AND %(week_end)s
  AND detected_at < %(as_of_at)s
ORDER BY receipt_date, rcept_no
