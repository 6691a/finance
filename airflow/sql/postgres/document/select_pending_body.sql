-- 본문을 아직 받아 보지 않은 문서. **이 조회가 곧 백필이다.**
--
-- `body_status IS NULL`이 "아직 시도하지 않았다"이고, 마이그레이션 직후에는 그 집합이 이미
-- 쌓인 문서 전부다. 신규와 과거가 같은 줄에 서므로 백필 스크립트를 따로 두지 않는다.
--
-- **`body IS NULL`로 고르지 않는다.** 그러면 받을 수 없는 문서(KRX처럼 문서별 딥링크가
-- 없는 것, 본문이 첨부에만 있는 것)를 매시간 다시 친다.
--
-- **본문 수집을 막아 둔 출처는 뺀다.** `collection_mode`를 내리는 것이 그 출처의 원문을
-- 그만 받는 유일한 손잡이다 — 이용조건이 문제가 되면 코드가 아니라 그 값을 내리는 것으로
-- 끝나야 한다는 것이 설계의 결정이다. 큐가 그 값을 안 보면 내려도 이 DAG은 계속 받는다.
-- `enabled`는 보지 않는다. 그것은 **새 문서를 발견할지**를 정하는 값이고, 여기는 이미
-- 발견해 저장한 행을 채우는 자리다.
--
-- 마스터에 행이 없는 출처는 그대로 받는다. `document`가 `document_source`로 외래키를 걸지
-- 않는 것과 같은 이유다 — 마스터가 비어도 수집이 멈추지 않아야 한다.
--
-- 대표에 연결된 중복은 뺀다. 대표가 본문을 갖는다.
--
-- 오래된 것부터 집는다. 백필이 하루면 끝나는 양이라 신규와 과거를 따로 줄 세우지 않는다
-- (`select_pending_assessment.sql`이 둘을 가르는 것과 다른 판단인데, 저기는 재평가 백로그가
-- 상시로 쌓이고 여기는 한 번뿐이다).
--
-- ponytail: 부분 인덱스 없이 순차 조회다. 문서가 수십만 건이 되어 이 조회가 느려지면
-- `(detected_at) WHERE body_status IS NULL` 부분 인덱스를 만든다.
SELECT
    d.id,
    d.source_slug,
    d.canonical_url
FROM document d
WHERE d.body_status IS NULL
  AND d.canonical_document_id IS NULL
  AND NOT EXISTS (
      SELECT 1
      FROM document_source s
      WHERE s.slug = d.source_slug
        AND s.collection_mode = 'metadata_only'
  )
ORDER BY d.detected_at, d.id
LIMIT %s
