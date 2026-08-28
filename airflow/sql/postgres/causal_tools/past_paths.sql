-- 이 대상에 **과거 주가 이은 경로**. 사건, 사슬, 방향, 확신, 실현 등락.
--
-- **어휘 후보가 이름만 주던 것을 실제 쓰임으로 바꾼다.** 프롬프트의 `기존 경로 후보`는
-- `c:4 — 금리 기대`처럼 이름뿐이라, 그 이름이 전에 어느 사건에서 어느 대상에 어떻게
-- 닿았는지를 모델이 알 수 없었다. 그래프를 잇는 것이 이 설계의 목적인데 이으려면 무엇이
-- 이미 이어져 있는지 봐야 한다.
--
-- **대상 주 이전만 본다.** 같은 주의 자기 경로를 보여 주면
-- 아직 만들지도 않은 것을 참조하게 되고, 재실행 때만 값이 달라진다.
--
-- 몇 주를 거슬러 볼지는 부르는 쪽이 정하고 상한은 코드 상수다.
SELECT p.week_start,
       e.title AS event_title,
       e.occurred_on,
       (SELECT string_agg(ch.name, ' > ' ORDER BY s.position)
          FROM market_causal_step s
          JOIN market_channel ch ON ch.id = s.channel_id
         WHERE s.path_id = p.id) AS chain,
       p.sign,
       p.confidence,
       p.return_week_change,
       p.return_t1_change,
       p.return_t5_change,
       p.return_unit,
       p.reasoning
FROM market_causal_path p
JOIN market_event e ON e.id = p.event_id
WHERE p.target_code = %(code)s
  AND p.week_start >= %(since)s
  AND p.week_start < %(week_start)s
ORDER BY p.week_start DESC, p.id
LIMIT %(limit)s
