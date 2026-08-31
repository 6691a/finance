-- 아직 파싱해 보지 않은 첨부 PDF. **이 조회가 곧 백필이다.**
--
-- `parse_status IS NULL`이 "아직 해 보지 않았다"이고, 마이그레이션 직후에는 그 집합이 이미
-- 받아 둔 첨부 전부다. `document.body_status`가 본문 큐인 것과 같은 규칙이다.
--
-- **원본이 바뀐 첨부도 다시 집는다**(`parsed_sha256 IS DISTINCT FROM sha256`). 같은 URL이
-- 다른 파일을 돌려준 경우이고, 그때 옛 텍스트를 그대로 두면 다른 문서의 글이 남는다.
-- 실패로 확정한 첨부(`failed`·`unsupported`)는 그 시점의 SHA를 함께 남기므로 파일이 바뀌지
-- 않는 한 다시 걸리지 않는다.
--
-- **PDF만 본다.** HWP·XLSX는 파서가 없다. 확장자와 미디어 타입을 둘 다 보는 이유는 제공처가
-- `Content-Type`을 주지 않는 경우가 있어서다(`document_body_hourly`가 그때 URL 경로에서
-- 확장자를 정한다).
--
-- 영상은 파일이 없으므로 `kind='file'`로 좁힌다.
--
-- ponytail: 부분 인덱스 없이 순차 조회다. 첨부가 수십만 건이 되어 느려지면
-- `(id) WHERE parse_status IS NULL` 부분 인덱스를 만든다.
SELECT
    a.id,
    a.document_id,
    a.storage_path,
    a.sha256
FROM document_attachment a
WHERE a.kind = 'file'
  AND a.storage_path IS NOT NULL
  AND a.sha256 IS NOT NULL
  AND (
      lower(a.storage_path) LIKE '%%.pdf'
      OR lower(coalesce(a.media_type, '')) = 'application/pdf'
  )
  AND (a.parse_status IS NULL OR a.parsed_sha256 IS DISTINCT FROM a.sha256)
ORDER BY a.id
LIMIT %s
