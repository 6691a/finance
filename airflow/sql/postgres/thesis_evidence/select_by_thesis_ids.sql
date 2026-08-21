-- 여러 추론의 근거 전부. 그래프 동기화가 읽는다.
--
-- 상위 몇 개로 자르지 않는다. Slack은 사람이 읽을 만큼만 보이면 되지만
-- (`select_top_by_thesis_ids.sql`) 그래프는 인용 관계 전부가 있어야 "무엇이 무엇을
-- 인용했나"에 답한다.
SELECT thesis_id,
       evidence_kind,
       evidence_ref,
       evidence_title,
       evidence_url,
       rank
FROM thesis_evidence
WHERE thesis_id = ANY(%s)
ORDER BY thesis_id, rank
