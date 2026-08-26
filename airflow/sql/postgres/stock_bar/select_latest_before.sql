-- 종목의 **기준 시각 직전 봉** 하나씩. 장중 추론의 현재가이자 예측 기준가다.
-- 규칙은 `index_bar/select_latest_before.sql`과 같고 표만 다르다.
--
-- **거래소는 KRX만이다.** NXT는 같은 종목의 별도 체결이라 섞으면 같은 시각에 값이 둘이
-- 된다(`stock_bar` 자연키에 거래소가 들어 있는 이유와 같다). 정규장 안에서 도는 슬롯이라
-- 볼 곳은 KRX다.
--
-- `is_final`은 보지 않는다. 장중 봉은 WebSocket 잠정이 정상이고
-- `kis_equity_bar_reconcile`이 매시 05·35분에 REST 확정으로 갈아 끼운다.
SELECT DISTINCT ON (stock_code)
       stock_code,
       bar_at,
       close,
       previous_close
FROM stock_bar
WHERE provider = 'kis'
  AND exchange = 'KRX'
  AND stock_code = ANY(%s)
  AND bar_at < %s
  AND bar_at >= %s
ORDER BY stock_code, bar_at DESC
