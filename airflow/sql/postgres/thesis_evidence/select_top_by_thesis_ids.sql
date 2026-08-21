-- 추론마다 상위 근거 몇 개. Slack 발송이 읽는다.
--
-- 상한을 SQL이 정하는 이유는 추론 수 × 근거 수만큼 행을 가져와 파이썬에서 자르면 발송에
-- 필요 없는 근거까지 매번 실어 오기 때문이다. 개수는 파라미터로 받는다.
SELECT thesis_id,
       evidence_kind,
       evidence_ref,
       evidence_title,
       evidence_url,
       rank
FROM (
    SELECT thesis_id,
           evidence_kind,
           evidence_ref,
           evidence_title,
           evidence_url,
           rank,
           row_number() OVER (PARTITION BY thesis_id ORDER BY rank) AS position
    FROM thesis_evidence
    WHERE thesis_id = ANY(%s)
) AS ranked
WHERE position <= %s
ORDER BY thesis_id, rank
