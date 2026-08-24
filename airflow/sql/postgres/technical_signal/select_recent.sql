-- 브리핑 표의 `신호` 열. 대상마다 **가장 최근 사건 하나**다.
--
-- 창은 달력일이다. 영업일로 세지 않는 이유는 이게 표시용이기 때문이다 — "최근에 뭐가
-- 있었나"를 보여 주는 칸이라 하루 이틀 경계가 흔들려도 읽는 사람의 판단이 달라지지 않는다.
-- 채점(문서 12.6절)은 반대로 영업일로 센다. 거기서는 T+N의 N이 값의 뜻을 정한다.
--
-- 같은 날 두 종류가 났으면 그중 하나만 나온다. 표의 한 칸에 둘을 넣을 수 없고, 이력 전체는
-- 추론 툴(`select_thesis_recent.sql`)이 준다.
--
-- 주석에 퍼센트 기호를 쓰지 않는다. psycopg가 주석까지 훑어 플레이스홀더로 센다.
SELECT DISTINCT ON (symbol)
       symbol,
       signal_date,
       kind,
       direction
FROM technical_signal
WHERE signal_date >= %(since_date)s
ORDER BY symbol, signal_date DESC, kind
