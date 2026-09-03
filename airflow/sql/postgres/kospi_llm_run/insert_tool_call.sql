-- 그 대화 안의 툴 호출 하나. 대화를 닫는 트랜잭션에서 함께 쓴다.
--
-- **검증 전 인자와 검증 후 인자를 둘 다 남긴다.** 모델이 보낸 그대로가 `arguments`이고
-- 함수에 실제로 들어간 것이 `validated_arguments`다. 둘이 다르면 스키마가 값을 고친 것이고,
-- 뒤가 NULL이면 함수에 닿지도 못한 것이다.
--
-- **결과 전문을 남긴다.** `document`는 upsert로 덮어써서 모델이 실제로 본 스냅샷이 이 행
-- 말고는 남지 않는다.
--
-- `delivered`는 결과가 모델 대화에 실제로 돌아갔는지다. sibling 실패로 버려진 결과는
-- 오류가 아니라 "모델만 못 봤다"이고, 인용 분석이 그 구분 위에 선다.
INSERT INTO kospi_tool_call (
    llm_run_id,
    seq,
    round_no,
    tool_call_id,
    tool_name,
    arguments,
    validated_arguments,
    requested_at,
    duration_ms,
    result,
    result_chars,
    error_kind,
    error,
    delivered
) VALUES (
    %(llm_run_id)s,
    %(seq)s,
    %(round_no)s,
    %(tool_call_id)s,
    %(tool_name)s,
    %(arguments)s,
    %(validated_arguments)s,
    %(requested_at)s,
    %(duration_ms)s,
    %(result)s,
    %(result_chars)s,
    %(error_kind)s,
    %(error)s,
    %(delivered)s
)
ON CONFLICT ON CONSTRAINT uq_kospi_tool_call_natural_key DO NOTHING
