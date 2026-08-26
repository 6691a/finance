-- 대화 하나가 부른 툴 하나를 남긴다.
--
-- **결과 본문을 전문으로 담는다.** `document`는 upsert로 덮어써서 그때 값을 복원할 수 없고,
-- 이 행이 모델이 실제로 본 스냅샷의 유일한 사본이 된다. 실측 중복은 작다 —
-- `document.body`가 비어 있어(수집이 metadata_only) 문서 하나당 제목 42자 + 요약 271자다.
--
-- `result`는 jsonb가 아니라 text다. 성공이면 JSON 문자열이지만 실패 본문은 평문이라
-- 굳힐 수 없다(`source_record.payload`가 JSON이 아닌 원본을 안 받는 것과 같은 판단).
-- 분석은 `WHERE error IS NULL` 뒤에 `result::jsonb`를 쓴다.
--
-- **`ON CONFLICT`가 없다.** 같은 (llm_run_id, seq)를 두 번 쓰는 경로가 없어야 하고,
-- 생기면 조용히 넘어가는 것보다 UNIQUE 위반으로 죽는 편이 낫다.
--
-- 정의의 원본은 `apps/models/analysis/thesis.py`의 `ThesisToolCall`이다.
INSERT INTO thesis_tool_call (
    llm_run_id,
    seq,
    round_no,
    tool_call_id,
    tool_name,
    arguments,
    validated_arguments,
    requested_at,
    duration_ms,
    result_chars,
    result,
    delivered,
    error_kind,
    error
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
