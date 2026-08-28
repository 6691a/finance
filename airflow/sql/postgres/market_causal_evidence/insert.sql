-- 경로가 근거로 든 후보 하나. `ref`는 `<kind>:<id>` 규약이다.
--
-- **헤더와 같은 트랜잭션에 쓴다.** 경로만 들어가고 근거가 빠진 상태를 남기지 않는다 —
-- 그러면 판정을 되짚을 수 없고, 그것이 이 테이블을 만든 이유다.
--
-- 목록 밖 ref는 여기 오기 전에 버려진다(`causal.generation.verify_paths`). 그래서
-- 마스터로 외래키를 걸지 않는다 — 근거가 세 테이블에 흩어져 있어 걸 대상도 하나가 아니다.
--
-- `ON CONFLICT DO NOTHING`은 같은 트랜잭션이 실패해 재시도할 때를 위한 것이다.
--
-- 정의의 원본은 `apps/models/analysis/causal.py`의 `MarketCausalEvidence`다.
INSERT INTO market_causal_evidence (path_id, ref)
VALUES (%s, %s)
ON CONFLICT ON CONSTRAINT uq_market_causal_evidence_natural_key DO NOTHING
