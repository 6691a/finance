-- 이벤트 판정이 쓰는 실적 실제값. 한 (종목, 기간 종료일, 지표)의 후보 행 전부를 돌려주고
-- 고르는 규칙(연결 CFS 우선, 최신 접수번호, 기간 기준)은 순수 함수가 갖는다.
-- created_at은 우리가 그 공시를 파싱한 시각(UTC)이라 발표 감지 시각의 대용이다.
SELECT
    id,
    statement_scope,
    amount_basis,
    release_type,
    rcept_no,
    current_amount,
    created_at
FROM earnings_fact
WHERE stock_code = %s
  AND period_end = %s
  AND metric = %s
ORDER BY rcept_no DESC, id DESC
