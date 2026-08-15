-- 평가 결과를 문서에 적는다. 문서를 버리거나 상태를 바꾸지 않는다.
-- `assessed_content_hash`는 어떤 본문으로 평가했는지를 남겨 재평가 판단의 기준이 된다.
UPDATE document
SET direction = %s,
    value_score = %s,
    assessment = %s::jsonb,
    llm_model = %s,
    prompt_version = %s,
    assessed_content_hash = %s,
    assessed_at = %s,
    updated_at = now()
WHERE id = %s
