-- 파싱 결과를 첨부 행에 채운다. 행은 본문·첨부 수집이 이미 만들었으므로 UPDATE다.
--
-- **마지막 WHERE 줄이 이 문장의 값어치다.** 우리가 읽은 SHA와 행의 sha256이 같을 때만
-- 갱신한다. 파일을 읽는 동안 같은 URL이 다른 파일로 바뀌어 행이 갱신됐다면 우리가 만든
-- 텍스트는 이미 다른 문서의 것이다. 그때는 아무 행도 갱신되지 않고, 다음 실행이 새 파일로
-- 다시 파싱한다.
--
-- 정의의 원본은 `apps/models/content.py`의 `DocumentAttachment`이고
-- `tests/collectors/test_attachment_pdf.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
UPDATE document_attachment
SET parse_status = %(parse_status)s,
    extracted_text = %(extracted_text)s,
    parsed_sha256 = %(parsed_sha256)s,
    parser_version = %(parser_version)s,
    parsed_at = now(),
    page_count = %(page_count)s,
    unreadable_page_count = %(unreadable_page_count)s,
    updated_at = now()
WHERE id = %(attachment_id)s
  AND sha256 = %(parsed_sha256)s
