-- 추론이 프롬프트에서 본 과거 추론 하나를 잇는다. 테이블은 백엔드 마이그레이션이 만든다.
--
-- **thesis와 같은 트랜잭션에 쓴다.** 추론만 들어가고 "무엇을 보고 냈나"가 빠진 상태를
-- 남기지 않는다. `ON CONFLICT DO NOTHING`은 같은 트랜잭션이 실패해 재시도할 때를 위한 것이다.
--
-- `thesis_evidence`가 아니다. 근거는 모델이 **인용한** 것이고 이것은 우리가 **보여 준**
-- 것이다. 순서(rank)도 없다 — 순서는 precedent의 run_date가 말한다.
--
-- 정의의 원본은 `apps/models/analysis/thesis.py`의 `ThesisPrecedent`이고
-- `tests/modules/test_thesis.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO thesis_precedent (thesis_id, precedent_id)
VALUES (%s, %s)
ON CONFLICT DO NOTHING
