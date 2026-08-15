-- 어떤 시계열이 있고 언제부터 언제까지 몇 건인지. LLM에게 주는 카탈로그다.
--
-- 라벨은 마스터에서 가져온다. `indicator_series`는 금리, `quote_symbol`은 지수·환율·원자재,
-- `instrument`는 종목이다. 어디에도 없으면 series_id를 그대로 쓴다(하나은행 통화 코드).
--
-- 관측 수와 구간을 함께 주는 이유는 **표본이 짧은 계열을 모델이 알고 고르게 하기 위해서다.**
-- 24일짜리로 낸 상관은 숫자만 있고 뜻이 없다.
SELECT
    s.provider,
    s.series_id,
    coalesce(i.label, q.label, n.name, s.series_id) AS label,
    s.kind,
    min(s.business_date) AS first_date,
    max(s.business_date) AS last_date,
    count(*) AS observations
FROM daily_series s
LEFT JOIN indicator_series i ON i.provider = s.provider AND i.series_id = s.series_id
LEFT JOIN quote_symbol q ON q.provider = s.provider AND q.symbol = s.series_id
LEFT JOIN instrument n ON n.ticker = s.series_id
WHERE (%s::text IS NULL OR s.kind = %s)
  AND (%s::text IS NULL OR s.series_id ILIKE '%%' || %s || '%%'
       OR coalesce(i.label, q.label, n.name, '') ILIKE '%%' || %s || '%%')
GROUP BY s.provider, s.series_id, i.label, q.label, n.name, s.kind
ORDER BY s.kind, s.provider, s.series_id
