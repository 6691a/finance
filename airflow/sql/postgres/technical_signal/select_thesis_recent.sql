-- 추론 툴 `daily_history`가 쓴다. 심볼 하나의 최근 매매 신호 이력.
--
-- **브리핑 쿼리를 재사용하지 않는다.** 브리핑은 지금까지를 보고 대상마다 한 건만 보이지만,
-- 추론은 기준 시각까지만 보고 이력 전체를 본다. 한쪽에 상한을 얹어 공유하면 다른 쪽이
-- 조용히 따라 바뀐다(프로젝트 규칙).
--
-- **cutoff는 `created_at`이다.** 신호는 마감 뒤 계산돼 들어오므로 `signal_date`로만 걸면
-- 장후 슬롯(15:30)이 아직 계산되지 않은 당일 신호를 본 것으로 읽는다.
--
-- `id`를 함께 준다. 모델이 `technical_signal:<id>`로 인용하면 그대로 `thesis_evidence`가 된다.
--
-- 주석에 퍼센트 기호를 쓰지 않는다. psycopg가 주석까지 훑어 플레이스홀더로 센다.
SELECT id,
       symbol,
       signal_date,
       kind,
       direction,
       close,
       rsi14,
       volume_ratio20
FROM technical_signal
WHERE symbol = %(symbol)s
  AND signal_date >= %(since_date)s
  AND created_at <= %(as_of_at)s
ORDER BY signal_date DESC, kind
LIMIT %(limit)s
