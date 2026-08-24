-- 추론 툴 `daily_history`가 **0행일 때만** 부른다. 일봉이 실제로 있는 심볼 목록.
--
-- 빈 배열을 그냥 주면 모델이 "이력이 없다"가 아니라 "움직임이 없었다"로 읽을 수 있다.
-- 그래서 빈 결과에 쓸 수 있는 심볼을 함께 준다. 왕복 하나를 아끼고, 무엇보다 빈 결과의
-- 뜻을 모델이 정확히 알게 한다.
--
-- `select_history.sql`과 같은 두 원천을 본다. 한쪽만 보면 "없다"는 답이 거짓이 된다.
WITH available AS (
    SELECT provider, symbol
    FROM quote_daily
    WHERE created_at <= %(as_of_at)s

    UNION

    SELECT provider, stock_code AS symbol
    FROM stock_investor_trade_daily
    WHERE provider = 'kis'
      AND created_at <= %(as_of_at)s
)
SELECT symbol.symbol,
       symbol.label,
       symbol.kind
FROM available
JOIN quote_symbol AS symbol
  ON symbol.provider = available.provider
 AND symbol.symbol = available.symbol
ORDER BY symbol.kind, symbol.symbol
