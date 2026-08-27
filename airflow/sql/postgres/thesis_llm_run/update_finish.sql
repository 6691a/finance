-- 열어 둔 대화를 닫는다. `succeeded`면 error가 NULL, `failed`면 사유가 있어야 한다
-- (`ck_thesis_llm_run_status_shape`).
--
-- **`WHERE status = 'running'`이다.** 이미 닫힌 대화를 다시 닫지 않는다 — 같은 대화를 두
-- 번 닫는 경로는 없어야 하고, 생기면 조용히 덮는 것보다 0행이 낫다.
--
-- `tool_rounds`는 그래프 최종 상태가 아니라 툴박스의 `round_count`에서 온다. 실패한
-- 대화는 최종 상태를 못 받기 때문이다.
--
-- `tool_result_chars`는 **모델에게 실제로 돌아간 것만** 센다(`delivered = true`).
-- 툴박스의 예산 카운터와 다른 수이고, `MAX_TOOL_RESULT_CHARS`와 직접 비교하지 않는다.
--
-- `investigation_truncated`는 모델이 툴을 더 부르겠다고 했는데 `MAX_TOOL_ROUNDS`에서
-- 끊긴 실행인지다. 끊기면 조용히 답변으로 넘어가므로 `tool_rounds`만으로는 스스로 끝낸
-- 실행과 구분되지 않는다. 해설 경로는 왕복 상한이 없어 언제나 false다.
UPDATE thesis_llm_run
SET status = %s,
    finished_at = %s,
    error = %s,
    tool_rounds = %s,
    tool_calls = %s,
    tool_result_chars = %s,
    investigation_truncated = %s,
    updated_at = now()
WHERE id = %s
  AND status = 'running'
