-- 실적 숫자를 아직 못 얻은 공시. 이 조회가 재시도 목록이라 별도 작업 큐가 필요 없다.
--
-- 원문이 아직 안 올라왔거나(014) 재무제표 API가 아직 그 접수번호로 답하지 않는 공시가
-- 여기 남는다. 다음 폴링이 다시 시도한다.
SELECT
    corp_code,
    company_name,
    stock_code,
    corp_class,
    report_name,
    rcept_no,
    filer_name,
    receipt_date,
    remarks
FROM disclosure_event
WHERE provider = 'dart'
  AND stock_code = ANY(%s)
  AND receipt_date >= %s
  AND NOT EXISTS (
      SELECT 1
      FROM earnings_fact
      WHERE earnings_fact.provider = disclosure_event.provider
        AND earnings_fact.rcept_no = disclosure_event.rcept_no
  )
ORDER BY receipt_date, rcept_no
