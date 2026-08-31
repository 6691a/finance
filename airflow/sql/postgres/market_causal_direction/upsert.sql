-- 대상 하나의 주간 방향성을 쓴다. 설계는
-- `docs/analysis/market-thesis/17-graph-query.md` §3.2다.
--
-- **`DO UPDATE`다.** 인과 경로(`market_causal_path/insert.sql`)와 반대 판단이고 이유가 있다 —
-- 저쪽은 LLM이 낸 최초 판단이라 덮어쓰면 사라지지만, 이 행은 그 경로들을 **접은 파생 요약**이라
-- 그래프가 다시 밀리면(`sync_only`) 따라가야 맞다. 추론이 그때 무엇을 봤나는
-- `thesis.input_state`에 관측 상태가 통째로 박혀 이미 남는다.
--
-- 세기 셋과 `path_ids`·`channels`는 코드가 Cypher 결과에서 만든다. `bias`와 `reasoning`만
-- LLM이 낸 값이다(설계 §3.1) — 숫자는 모델이 만들지 않는다.
--
-- 정의의 원본은 `apps/models/analysis/causal.py`의 `MarketCausalDirection`이다.
INSERT INTO market_causal_direction (
    week_start,
    target_kind,
    target_code,
    bias,
    reasoning,
    up_count,
    down_count,
    flat_count,
    path_ids,
    channels,
    llm_run_id
) VALUES (
    %(week_start)s,
    %(target_kind)s,
    %(target_code)s,
    %(bias)s,
    %(reasoning)s,
    %(up_count)s,
    %(down_count)s,
    %(flat_count)s,
    %(path_ids)s,
    %(channels)s,
    %(llm_run_id)s
)
ON CONFLICT (week_start, target_kind, target_code) DO UPDATE SET
    bias = EXCLUDED.bias,
    reasoning = EXCLUDED.reasoning,
    up_count = EXCLUDED.up_count,
    down_count = EXCLUDED.down_count,
    flat_count = EXCLUDED.flat_count,
    path_ids = EXCLUDED.path_ids,
    channels = EXCLUDED.channels,
    llm_run_id = EXCLUDED.llm_run_id,
    updated_at = now()
RETURNING id
