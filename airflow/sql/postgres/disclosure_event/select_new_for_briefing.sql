-- `slack_disclosure_briefing`이 읽는, 이 실행 창에서 처음 감지된 공시.
--
-- 창은 DAG의 data interval이다. `(window_start, window_end]` 반열림이라 경계의 한 건이
-- 두 창에 걸치지 않는다. 벽시계가 아니라 interval이므로 실행이 밀려도 창이 이어진다.
--
-- `receipt_date`가 아니라 `detected_at`으로 거른다. 접수일은 날짜뿐이라 시각으로 자를 수
-- 없고, 우리가 실제로 알 수 있었던 시점은 감지 시각이다(`select_recent.sql`과 같은 판단).
--
-- **추론 툴의 `select_recent.sql`을 재사용하지 않는다.** 그쪽은 슬롯의 `as_of_at`까지
-- 되돌아보는 조회이고 종목 필터와 건수 상한이 여기와 다르다. 한쪽을 고칠 때 다른 쪽이
-- 조용히 따라 바뀌지 않게 파일을 나눈다(저장소 규칙).
--
-- 오름차순이다. 채널에는 올라온 순서대로 그린다.
--
-- 뷰어 URL은 여기서 만들지 않는다. 접수번호만 주고 `modules/briefing/disclosures.py`가 붙인다.
WITH bounds AS (
    SELECT %s::timestamptz AS window_start,
           %s::timestamptz AS window_end
)
SELECT disclosure_event.rcept_no,
       disclosure_event.stock_code,
       disclosure_event.company_name,
       disclosure_event.report_name,
       disclosure_event.receipt_date,
       disclosure_event.detected_at,
       disclosure_event.remarks
FROM disclosure_event
CROSS JOIN bounds
WHERE disclosure_event.provider = 'dart'
  AND disclosure_event.detected_at > bounds.window_start
  AND disclosure_event.detected_at <= bounds.window_end
ORDER BY disclosure_event.detected_at, disclosure_event.rcept_no
