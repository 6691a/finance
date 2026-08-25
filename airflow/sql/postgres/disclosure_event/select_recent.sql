-- 추론 툴 `recent_disclosures`가 읽는 추적 종목의 최근 공시.
--
-- 창의 끝은 슬롯이 정한 `as_of_at`이다. 술어는 `detected_at` 하나로 충분하다 —
-- 공시 행은 접수번호 단위로 한 번 쓰고 갱신하지 않아서 `updated_at`이 움직이지 않는다.
--
-- `receipt_date`가 아니라 `detected_at`으로 거른다. 접수일은 날짜뿐이라 창의 끝을 시각으로
-- 자를 수 없고, 우리가 실제로 알 수 있었던 시점은 감지 시각이다.
--
-- 뷰어 URL은 여기서 만들지 않는다. 접수번호만 주고 `modules/thesis_toolbox.py`가 붙인다.
WITH bounds AS (
    SELECT %s::timestamptz AS window_start,
           %s::timestamptz AS as_of_at
)
SELECT disclosure_event.rcept_no,
       disclosure_event.stock_code,
       disclosure_event.company_name,
       disclosure_event.report_name,
       disclosure_event.receipt_date,
       disclosure_event.detected_at
FROM disclosure_event
CROSS JOIN bounds
WHERE disclosure_event.provider = 'dart'
  AND disclosure_event.stock_code = ANY(%s)
  AND disclosure_event.detected_at >= bounds.window_start
  AND disclosure_event.detected_at <= bounds.as_of_at
ORDER BY disclosure_event.detected_at DESC
LIMIT %s
