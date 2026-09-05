-- 모델이 근거로 든 검색 결과에 표시한다. 검증이 끝난 뒤에 찍는다.
--
-- URL로 찍는 이유는 모델이 인덱스로 답하고 그 인덱스를 코드가 URL로 되돌려 주기 때문이다.
-- 인덱스는 그 시도 안에서만 뜻이 있고 URL은 행의 자연키라 다음에도 같은 것을 가리킨다.
UPDATE market_shock_search_hit
SET cited = true
WHERE shock_event_id = %(shock_event_id)s
  AND url = ANY(%(urls)s)
