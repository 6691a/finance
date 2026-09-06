-- 모델에게 안 보낸 문서를 점수 없이 닫는다. `modules/untrusted.py`의 의심 문구에 걸린 것이다.
-- 방향·점수·모델은 NULL로 둔다 — 모델이 안 낸 값을 지어내지 않는다. 점수 하한을 보는
-- 소비자(브리핑 후보·급변 원인·코스피 툴)는 NULL을 자연히 거른다.
-- 판과 해시를 적어 다음 실행이 다시 집지 않고, 판이 오르면 다시 본다.
UPDATE document
SET direction = NULL,
    value_score = NULL,
    assessment = %s::jsonb,
    llm_model = NULL,
    prompt_version = %s,
    assessed_content_hash = %s,
    assessed_at = %s,
    updated_at = now()
WHERE id = %s
