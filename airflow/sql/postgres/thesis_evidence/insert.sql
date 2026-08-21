-- 추론이 인용한 근거 한 건을 저장한다. 테이블은 백엔드 마이그레이션이 만든다.
--
-- **INSERT만 한다.** 추론 행이 불변이라 근거를 교체할 일이 없다. thesis와 같은 트랜잭션에
-- 써서 추론만 들어가고 근거가 빠진 상태를 남기지 않는다.
--
-- `ON CONFLICT DO NOTHING`은 같은 트랜잭션이 실패해 재시도할 때를 위한 것이다. 자연키가
-- 둘(ref 중복, rank 중복)이라 제약을 지정하지 않고 둘 다 흡수한다.
--
-- 정의의 원본은 `apps/models/analysis.py`의 `ThesisEvidence`이고
-- `tests/modules/test_thesis.py`가 여기 컬럼을 그 모델 metadata와 대조한다.
INSERT INTO thesis_evidence (
    thesis_id,
    evidence_kind,
    evidence_ref,
    evidence_title,
    evidence_url,
    detail,
    rank
) VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
