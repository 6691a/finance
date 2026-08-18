-- 심볼마다 최근 며칠의 일별 종가. 브리핑이 "오늘 움직임이 큰가"를 답하는 근거다.
--
-- 두 테이블을 합치는 이유: 일봉 테이블(quote_daily)은 yahoo만 채운다. 국내 지수·선물은
-- 1분봉만 있어 여기서 하루의 마지막 봉을 골라 일별 종가로 쓴다.
--
-- 국내 쪽 날짜 경계는 KST다. kis 심볼은 전부 국내라 시장이 하나이고, 그래서 심볼마다
-- 시간대를 따질 필요가 없다. 해외는 수집기가 이미 그 시장의 현지 날짜로 business_date를
-- 계산해 두었으므로 그대로 쓴다.
SELECT provider, symbol, on_date, close
FROM (
    SELECT provider,
           symbol,
           business_date AS on_date,
           close
    FROM quote_daily
    WHERE business_date >= %s

    UNION ALL

    SELECT DISTINCT ON (provider, symbol, (bar_at AT TIME ZONE 'Asia/Seoul')::date)
           provider,
           symbol,
           (bar_at AT TIME ZONE 'Asia/Seoul')::date AS on_date,
           close
    FROM quote_bar
    WHERE provider = 'kis'
      AND bar_at >= %s
    ORDER BY provider, symbol, (bar_at AT TIME ZONE 'Asia/Seoul')::date, bar_at DESC
) AS daily
ORDER BY provider, symbol, on_date
