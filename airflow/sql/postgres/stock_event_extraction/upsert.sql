-- 문서 하나의 추출 원장. 본문이 바뀌어 다시 뽑으면 같은 행을 갱신한다.
-- 정의의 원본은 `apps/models/analysis.py`의 `StockEventExtraction`이다.
INSERT INTO stock_event_extraction (
    document_id,
    extracted_content_hash,
    extracted_at,
    llm_model,
    prompt_version,
    claim_count
)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (document_id) DO UPDATE SET
    extracted_content_hash = EXCLUDED.extracted_content_hash,
    extracted_at = EXCLUDED.extracted_at,
    llm_model = EXCLUDED.llm_model,
    prompt_version = EXCLUDED.prompt_version,
    claim_count = EXCLUDED.claim_count,
    updated_at = now()
