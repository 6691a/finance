-- 한 주의 경로 헤더를 그래프 투영에 필요한 것만 골라 읽는다(설계 4-graph.md §2).
--
-- **미러가 아니라 projection이다.** `input_hash`·`llm_run_id`·`reasoning` 밖의 감사 값은
-- 싣지 않는다. 그래프로 답하는 질문은 "무엇이 무엇을 통해 무엇에 닿았나"까지이고 그 이상은
-- Postgres를 본다.
--
-- 출발점이 사건일 수도 대상일 수도 있어서 `market_event`는 LEFT JOIN이다. 둘 중 정확히
-- 하나가 채워진 것은 `ck_market_causal_path_source_exclusive`가 DB에서 보장한다.
--
-- 실현 등락 셋은 `HITS` 엣지의 속성이 된다. **`return_unit`을 함께 싣는다** — percent와
-- basis_point가 한 칸에 섞이면 크기 비교가 조용히 무의미해진다.
--
-- `created_at`은 모든 엣지에 실린다. 추론 툴이 "그 슬롯 시각에 이미 있던 경로"만 보게 하는
-- event-time cutoff의 축이다(17-graph-query.md §5.3). `week_start`는 그 용도로 못 쓴다 —
-- 경로는 그 주가 끝나고 한 주 뒤에, 재실행이면 아무 때나 생긴다.
SELECT p.id AS path_id,
       p.week_start,
       p.created_at,
       e.title AS event_title,
       e.occurred_on AS event_occurred_on,
       p.source_target_kind,
       p.source_target_code,
       p.source_sign,
       p.target_kind,
       p.target_code,
       p.sign,
       p.confidence,
       p.reasoning,
       p.return_week_change,
       p.return_t1_change,
       p.return_t5_change,
       p.return_unit
  FROM market_causal_path p
  LEFT JOIN market_event e ON e.id = p.event_id
 WHERE p.week_start = %(week_start)s
 ORDER BY p.id
