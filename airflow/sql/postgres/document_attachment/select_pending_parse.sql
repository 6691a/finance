-- 아직 파싱해 보지 않은 첨부 PDF. **이 조회가 곧 백필이다.**
--
-- `parsed_sha256 IS DISTINCT FROM sha256` 한 줄이 "아직 해 보지 않았다"(`parsed_sha256`만 NULL)와
-- "원본이 바뀌었다"를 함께 집는다. 마이그레이션 직후에는 그 집합이 이미 받아 둔 첨부 전부다.
-- `document.body_status`가 본문 큐인 것과 같은 규칙이다. 실패로 확정한 첨부(`failed`·
-- `unsupported`)도 그 시점의 SHA를 남기므로 파일이 바뀌지 않는 한 다시 걸리지 않는다.
--
-- **파서 판이 오르면 전부 다시 집는다**(`parser_version IS DISTINCT FROM`). 규칙이 바뀐 뒤 옛
-- 판으로 만든 텍스트를 그대로 두면 한 색인 안에 두 규칙의 표가 섞인다.
--
-- **PDF만 본다.** HWP·XLSX는 파서가 없다. 확장자는 `document_body_hourly`가 파일 이름과
-- `Content-Type`에서 정해 저장 경로에 붙인 것이라 경로 하나로 충분하다.
--
-- 영상은 파일이 없으므로 `kind='file'`로 좁힌다.
--
-- ponytail: 부분 인덱스 없이 순차 조회다. 첨부가 수십만 건이 되어 느려지면
-- `(id) WHERE parsed_sha256 IS DISTINCT FROM sha256` 부분 인덱스를 만든다.
SELECT
    a.id,
    a.storage_path
FROM document_attachment a
WHERE a.kind = 'file'
  AND a.storage_path IS NOT NULL
  AND a.sha256 IS NOT NULL
  AND lower(a.storage_path) LIKE '%%.pdf'
  AND (
      a.parsed_sha256 IS DISTINCT FROM a.sha256
      OR a.parser_version IS DISTINCT FROM %(parser_version)s
  )
ORDER BY a.id
LIMIT %(limit)s
