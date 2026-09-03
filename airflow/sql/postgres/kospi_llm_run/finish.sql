-- 대화를 닫는다. 성공이든 실패든 반드시 지나는 자리다.
--
-- **총량 둘은 상한을 재는 카운터와 다른 수다.** `tool_calls`는 기록된 행 수라 모르는 툴과
-- 인자 검증 실패도 세지만 툴박스의 예산 카운터는 함수에 진입한 것만 센다.
-- `tool_result_chars`는 모델에게 실제로 돌아간 것만 센다. 둘 다 `MAX_TOOL_*`와 직접
-- 비교하지 않는다.
--
-- 토큰 넷이 NULL이면 "안 쟀다"이고 0이면 "안 썼다"다. 모델을 한 번도 못 부르고 죽은
-- 대화는 0이다.
UPDATE kospi_llm_run
SET status = %(status)s,
    finished_at = %(finished_at)s,
    error = %(error)s,
    tool_rounds = %(tool_rounds)s,
    tool_calls = %(tool_calls)s,
    tool_result_chars = %(tool_result_chars)s,
    truncated = %(truncated)s,
    rejected = %(rejected)s,
    observations_written = %(observations_written)s,
    memories_written = %(memories_written)s,
    memories_rejected = %(memories_rejected)s,
    memories_kept = %(memories_kept)s,
    memories_dropped = %(memories_dropped)s,
    memories_unreviewed = %(memories_unreviewed)s,
    memories_expired = %(memories_expired)s,
    prompt_tokens = %(prompt_tokens)s,
    cached_tokens = %(cached_tokens)s,
    completion_tokens = %(completion_tokens)s,
    reasoning_tokens = %(reasoning_tokens)s,
    updated_at = now()
WHERE id = %(id)s
