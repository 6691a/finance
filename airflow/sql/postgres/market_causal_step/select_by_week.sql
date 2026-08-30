-- 한 주의 단계를 채널 이름과 함께 읽는다. 경로 헤더와 짝이 되어 엣지 N+1개가 된다.
--
-- `channel_id`가 아니라 이름을 준다. 그래프에서 채널 노드의 키가 이름이기 때문이다
-- (설계 4-graph.md §2 — 자연키를 그대로 노드 키로 쓴다).
--
-- `position` 순서가 곧 체인 순서다. 1부터 빈 곳 없이 채워지는 것은 저장 코드가 보장한다.
SELECT s.path_id,
       s.position,
       c.name AS channel
  FROM market_causal_step s
  JOIN market_channel c ON c.id = s.channel_id
  JOIN market_causal_path p ON p.id = s.path_id
 WHERE p.week_start = %(week_start)s
 ORDER BY s.path_id, s.position
