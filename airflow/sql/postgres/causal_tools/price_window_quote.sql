-- 매크로(해외 지수·환율·선물)의 **일별** 종가. 사건 전 구간부터 반응 끝까지.
--
-- `quote_daily`는 kind별 물리 테이블을 UNION ALL 한 읽기 전용 뷰다. 조회는 뷰를 써도 되고
-- 쓰기만 물리 테이블로 간다(저장소 규칙).
--
-- 나머지 판단은 `price_window_index.sql`과 같다.
SELECT business_date, close
FROM quote_daily
WHERE symbol = %(code)s
  AND business_date BETWEEN %(start)s AND %(end)s
  AND close IS NOT NULL
ORDER BY business_date
