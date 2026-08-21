-- 추론 툴 `recent_documents`가 읽는 최근 고득점 문서.
--
-- **창의 끝은 벽시계가 아니라 슬롯이 정한 `as_of_at`이다.** 술어를 event-time 컬럼 셋에
-- 모두 건다. `detected_at`은 언제 봤나, `assessed_at`은 언제 평가했나, `updated_at`은
-- 본문이 언제 갱신됐나다. `updated_at`까지 거는 것은 보수적 선택이다 — 본문이 갱신된 문서는
-- 과거 상태를 알 수 없으니 뺀다. 셋 다 걸어야 "그 시각에 알 수 있었던 것"에 가까워진다.
--
-- 이유 문장을 쓸 재료로 `new_facts`와 `reason`을 함께 준다. 제목·점수만 주면 모델이 근거를
-- 지어낸다. 둘 다 컬럼이 아니라 `assessment` JSONB 안의 키다.
--
-- 티커는 배열로 접어 문서당 한 행을 지킨다(`select_briefing_candidates.sql`과 같은 이유).
-- 대표에 연결된 중복은 뺀다. 같은 사건이 근거 목록을 채우면 인용이 낭비된다.
WITH bounds AS (
    SELECT %s::timestamptz AS window_start,
           %s::timestamptz AS as_of_at
)
SELECT document.id,
       document.title,
       document.canonical_url,
       document.source_slug,
       document.published_at,
       document.value_score,
       document.direction,
       document.assessment -> 'new_facts' AS new_facts,
       document.assessment ->> 'reason' AS reason,
       coalesce(
           array_agg(tag.ticker ORDER BY tag.ticker) FILTER (WHERE tag.ticker IS NOT NULL),
           '{}'
       ) AS tickers
FROM document
CROSS JOIN bounds
LEFT JOIN document_instrument AS tag
       ON tag.document_id = document.id
WHERE document.canonical_document_id IS NULL
  AND document.assessed_at IS NOT NULL
  AND document.detected_at >= bounds.window_start
  AND document.detected_at <= bounds.as_of_at
  AND document.assessed_at <= bounds.as_of_at
  AND document.updated_at <= bounds.as_of_at
  AND document.value_score >= %s
GROUP BY document.id
ORDER BY document.value_score DESC NULLS LAST, document.assessed_at DESC
LIMIT %s
