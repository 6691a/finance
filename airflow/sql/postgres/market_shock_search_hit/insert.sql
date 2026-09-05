-- 검색으로 만난 기사 하나를 영구 보관한다.
--
-- **받은 것을 전부 남긴다.** 인용된 것만 남기면 "검색은 했는데 쓸 게 없었다"를 못 세는데,
-- 그것이 검색을 계속 쓸지 정하는 신호다.
--
-- 자연키 `(shock_event_id, url)`이라 같은 기사가 여러 질의·여러 시도에서 나와도 한 행이고,
-- **처음 본 질의·시도·순위가 남는다**(`DO NOTHING`). 나중 시도가 순위를 덮어쓰면 "언제
-- 처음 이 기사를 봤나"가 사라진다.
INSERT INTO market_shock_search_hit (
    shock_event_id,
    provider,
    query,
    attempt,
    rank,
    title,
    url,
    publisher,
    published_at,
    snippet,
    relevance,
    retrieved_at
) VALUES (
    %(shock_event_id)s,
    %(provider)s,
    %(query)s,
    %(attempt)s,
    %(rank)s,
    %(title)s,
    %(url)s,
    %(publisher)s,
    %(published_at)s,
    %(snippet)s,
    %(relevance)s,
    %(retrieved_at)s
)
ON CONFLICT (shock_event_id, url) DO NOTHING
RETURNING id
