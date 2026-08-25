-- 중복 후보 두 갈래. 판정은 modules/dedup.py가 한다.
--
-- ① 같은 출처에서 발행(없으면 발견) 시각 ±12시간 안의 이웃. 제목 유사도로 가른다.
--    창이 12시간인 이유: 속보→본기사 간격(분~시간)은 덮고, `[표] 오늘의 환율` 같은
--    매일 반복 기사(24시간 간격)는 오판하지 않는다.
-- ② 출처와 무관하게 `content_hash`가 같고 ±72시간 안인 문서. 설계 §6.4 ②의 규칙 판정이라
--    제목을 보지 않는다. 해시는 제목·요약·본문을 이어 붙인 SHA-256이라 값이 같으면 셋이
--    글자 그대로 같다.
--
-- **②에 시각 창을 두는 이유는 실측이다**(2026-08-25 운영 DB, 문서 2,332건). 해시가 겹치는
-- 48묶음 중 출처가 둘 이상인 것은 0이었고, 창 없이 걸면 33일에 걸친 BOJ 통계 항목 4건
-- (요약·본문이 비어 제목만 같다)과 6일 간격 KIND 공시 2건이 한 문서로 묶인다. 전재는 시간
-- 단위 안에서 일어나므로 72시간이면 덮는다. 72는 설계 §6.4 ③의 기사 창과 같은 값이다.
--
-- canonical_document_id를 함께 내려 이미 연결된 문서의 root를 따라갈 수 있게 한다.
-- source_slug도 내린다 — ②로 걸린 후보는 요청한 출처와 다를 수 있다.
SELECT
    id,
    source_slug,
    title,
    published_at,
    detected_at,
    coalesce(length(summary), 0) + coalesce(length(body), 0) AS content_length,
    canonical_document_id,
    content_hash
FROM document
WHERE id <> %s
  AND (
      (
          source_slug = %s
          AND coalesce(published_at, detected_at)
              BETWEEN %s::timestamptz - interval '12 hours' AND %s::timestamptz + interval '12 hours'
      )
      OR (
          content_hash = %s
          AND coalesce(published_at, detected_at)
              BETWEEN %s::timestamptz - interval '72 hours' AND %s::timestamptz + interval '72 hours'
      )
  )
