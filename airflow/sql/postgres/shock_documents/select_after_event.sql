-- 원인 분석이 읽는 문서. **창의 하한이 포착 시각이다.**
--
-- 기존 추론의 `kospi_tools/select_news.sql`을 베끼지 않고 새로 만든다 — 저쪽은 "그 시각에
-- 알 수 있었던 것"을 보는 장중 조회이고 여기는 "그 사건 뒤에 나온 설명"을 보는 사후
-- 조회다. 한쪽을 고칠 때 다른 쪽이 조용히 따라 바뀌는 것을 막는다.
--
-- **사건 이전 문서를 아예 안 준다.** 재료는 대개 며칠 전부터 있다(2026-09-03 사건이면
-- 09-02의 우에다 발언). 그것을 근거로 받으면 "전부터 있던 것"이 그날 그 시각의 방아쇠로
-- 둔갑한다. 그래서 창을 여기서 자르고 검증에서 다시 보지 않는다.
--
-- 제목·점수만 주면 모델이 근거를 지어낸다. 평가가 남긴 `reason`과 `new_facts`를 함께
-- 준다 — 둘 다 컬럼이 아니라 `assessment` JSONB 안의 키다.
--
-- `detected_at`·`assessed_at` 상한은 그 시도 시각이다. 재실행이 나중 문서를 끌어와
-- 옛 판단을 다시 만들지 않게 한다.
SELECT document.id,
       document.published_at,
       document.source_slug,
       document.title,
       document.value_score,
       document.assessment ->> 'reason' AS reason,
       document.assessment -> 'new_facts' AS new_facts
FROM document
WHERE document.canonical_document_id IS NULL
  AND document.assessed_at IS NOT NULL
  AND document.published_at > %(event_at)s
  AND document.detected_at <= %(as_of_at)s
  AND document.assessed_at <= %(as_of_at)s
ORDER BY document.value_score DESC NULLS LAST, document.published_at DESC
LIMIT %(limit)s
