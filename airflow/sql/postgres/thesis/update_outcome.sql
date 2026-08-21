-- 추론 하나의 채점 결과를 채운다.
--
-- 채점 컬럼 넷만 건드린다. 추론 컬럼(확률·이유·근거)은 불변이라 여기서 손대지 않는다.
--
-- `evaluated_at IS NULL`이 재실행 멱등을 만든다. 이미 채점된 행은 0행이 갱신되고, 처음
-- 매긴 점수가 그대로 남는다. 등락률을 못 구한 행은 아예 부르지 않아 미채점(NULL 전부)으로
-- 남는다 — 0으로 꾸미지 않는다.
--
-- 값 넷은 전부 부르는 쪽이 계산해서 넘긴다(`modules/thesis.py`의 `classify_outcome`·
-- `brier_score`). SQL에 수식을 넣으면 DB 없이 경계값을 테스트할 수 없다.
UPDATE thesis
SET evaluated_at = %s,
    actual_return_pct = %s,
    actual_outcome = %s,
    brier_score = %s,
    updated_at = now()
WHERE id = %s
  AND evaluated_at IS NULL
