-- 추론 툴 `daily_history`가 쓴다. 심볼 하나의 최근 일봉 며칠치. 뷰는 읽기 전용이라 조회만 한다.
--
-- `macro_changes`는 창 하나의 양 끝만 준다. 그것으로는 "어제 하루 빠진 것"과 "닷새째
-- 빠지는 중"을 가를 수 없다. 이 툴이 그 추세를 준다.
--
-- **cutoff는 `created_at`이다.** 일봉은 마감 뒤에 들어오므로 `business_date`로만 걸면
-- 장전 슬롯이 아직 모르는 당일 봉을 본 것으로 읽는다.
--
-- 변화를 계산하지 않고 종가를 그대로 준다. 금리 계열을 퍼센트로 바꾸면 안 되는 것은
-- `quote_bar/select_window_changes.sql`과 같은 이유이고, 표기는 `modules/thesis.py`가 정한다.
--
-- 주석에 퍼센트 기호를 쓰지 않는다. psycopg가 주석까지 훑어 플레이스홀더로 센다.
SELECT bar.symbol,
       symbol.label,
       symbol.kind,
       symbol.country,
       bar.business_date,
       bar.open,
       bar.high,
       bar.low,
       bar.close,
       bar.volume
FROM quote_daily AS bar
JOIN quote_symbol AS symbol
  ON symbol.provider = bar.provider
 AND symbol.symbol = bar.symbol
WHERE bar.symbol = %(symbol)s
  AND bar.created_at <= %(as_of_at)s
ORDER BY bar.business_date DESC
LIMIT %(days)s
