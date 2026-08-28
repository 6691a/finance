-- 경로 하나의 헤더를 저장한다. 단계는 `market_causal_step/insert.sql`이 잇는다.
--
-- **upsert가 아니라 INSERT다.** 같은 자연키에 행이 이미 있으면 아무 것도 바꾸지 않고 0행을
-- 돌려준다. 첫 성공본이 불변인 이유는 `thesis/insert.sql`과 같다 — LLM은 재호출마다 답이
-- 달라서 덮어쓰면 최초 판단이 사라진다.
--
-- 자연키에 `chain_key`가 들어간다. 같은 사건이 같은 대상에 서로 다른 경로로 닿는 일이
-- 실제로 있어서다(금리 인상이 `할인율`로는 은행주를 누르고 `예대마진`으로는 올린다).
--
-- `return_unit`이 실현 등락 셋의 단위다. 가격은 percent, 금리는 basis_point다 — 한 칸에
-- 담으면 7bp와 10퍼센트가 섞여 크기 비교가 조용히 무의미해진다.
--
-- 정의의 원본은 `apps/models/analysis/causal.py`의 `MarketCausalPath`다.
INSERT INTO market_causal_path (
    week_start,
    event_id,
    target_kind,
    target_code,
    chain_key,
    sign,
    confidence,
    reasoning,
    return_week_change,
    return_t1_change,
    return_t5_change,
    return_unit,
    input_hash,
    llm_run_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT ON CONSTRAINT uq_market_causal_path_natural_key DO NOTHING
RETURNING id
