-- 추론 툴 `daily_history`가 **0행일 때만** 부른다. 일봉이 실제로 있는 심볼 목록.
--
-- 2026-08-21 실측: `quote_daily`에 KOSPI·KOSDAQ 일봉이 없다. 국내 지수는 분봉
-- (`index_bar`)만 수집하고 일봉 테이블(`index_daily`)에는 해외 지수만 들어 있다.
-- 모델이 KOSPI를 물으면 빈 배열을 받고 "국내 지수 이력이 없다"가 아니라 "움직임이
-- 없었다"로 읽을 수 있다.
--
-- 그래서 빈 결과에 쓸 수 있는 심볼을 함께 준다. 왕복 하나를 아끼고, 무엇보다 빈 결과의
-- 뜻을 모델이 정확히 알게 한다.
SELECT symbol.symbol,
       symbol.label,
       symbol.kind
FROM quote_symbol AS symbol
WHERE EXISTS (
    SELECT 1
    FROM quote_daily AS bar
    WHERE bar.provider = symbol.provider
      AND bar.symbol = symbol.symbol
      AND bar.created_at <= %(as_of_at)s
)
ORDER BY symbol.kind, symbol.symbol
