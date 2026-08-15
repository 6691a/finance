-- 기간과 태그로 문서를 뽑는다. 리포트가 기사를 고르는 경로다.
--
-- **본문을 주지 않는다.** 제목·요약·점수·방향만 돌려준다. 리포트 프롬프트에 수십 건이
-- 들어가므로 본문까지 실으면 창을 통째로 먹는다. 원문이 필요하면 canonical_url로 간다.
--
-- 종목·지표 태그는 `document_instrument`·`document_indicator`가 잇는다. 자유 문자열
-- 태그였다면 이 조인이 안 된다.
SELECT
    d.id,
    d.source_slug,
    d.published_at,
    d.title,
    d.summary,
    d.direction,
    d.value_score,
    d.canonical_url
FROM document d
WHERE d.canonical_document_id IS NULL
  AND (%s::timestamptz IS NULL OR d.published_at >= %s)
  AND (%s::timestamptz IS NULL OR d.published_at <= %s)
  AND (%s::int IS NULL OR d.value_score >= %s)
  AND (%s::text IS NULL OR EXISTS (
        SELECT 1 FROM document_instrument di WHERE di.document_id = d.id AND di.ticker = %s))
  AND (%s::text IS NULL OR EXISTS (
        SELECT 1 FROM document_indicator dx
        WHERE dx.document_id = d.id AND dx.provider = %s AND dx.series_id = %s))
ORDER BY d.value_score DESC NULLS LAST, d.published_at DESC NULLS LAST
LIMIT %s
