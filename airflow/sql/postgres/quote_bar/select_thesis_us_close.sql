-- 추론 툴 `us_market_close()`가 읽는다. 미국 심볼의 마감 값과 **전일 정규장 종가 대비** 변화.
-- 뷰는 읽기 전용이라 조회만 한다.
--
-- `select_window_changes.sql`과 무엇이 다른가: 저쪽은 분석 창의 첫 봉 대비 마지막 봉이다.
-- KIS 해외지수 현물처럼 마감 직전 두 시간만 쌓이는 심볼은 그 창 안에서 거의 움직이지 않아
-- 변화가 0에 가깝게 보인다(실측 2026-08-21 SPX: 창 첫 봉 7671.73, 마지막 봉 7674.37).
-- 밤사이 미국장이 얼마나 움직였나는 봉에 실려 오는 `previous_close`와 비교해야 나온다.
-- 그 값이 곧 Slack 미국장 브리핑 표에 그려지는 등락과 같은 수치다.
--
-- 심볼마다 창 안 **마지막 봉 하나**를 고른다. 미국 현물 마감 뒤에는 정산 구간 봉이
-- 이어지는데 그 마지막 봉이 공식 종가다(`modules/collectors/kis_overseas_index.py`).
--
-- **`bar_at + interval '1 minute' <= as_of_at`이다.** `bar_at`은 봉의 시작 시각이라
-- `bar_at <= as_of_at`으로 자르면 경계 봉이 담은 미래 1분이 섞인다.
--
-- 변화는 여기서 계산하지 않는다. 금리 계열은 퍼센트가 아니라 bp로 읽어야 해서
-- 표기는 `modules/thesis/toolbox.py`가 정한다(`select_window_changes.sql`과 같은 이유).
--
-- 크립토는 country가 `XX`라 여기 안 들어온다. 24시간 거래라 "마감"이 없고, 창 변화는
-- `macro_changes()`가 이미 준다.
--
-- **주석에는 퍼센트 기호를 아예 쓰지 않는다.** psycopg는 주석까지 훑어 플레이스홀더를
-- 센다. 주석 안의 것도 자리 수에 들어가 실행이 거절된다(2026-08-21 실측).
WITH bounds AS (
    SELECT %(window_start)s::timestamptz AS window_start,
           %(as_of_at)s::timestamptz AS as_of_at
),
closing AS (
    SELECT DISTINCT ON (bar.provider, bar.symbol)
           bar.provider,
           bar.symbol,
           symbol.label,
           symbol.kind,
           bar.close,
           bar.previous_close,
           bar.bar_at
    FROM quote_bar AS bar
    CROSS JOIN bounds
    JOIN quote_symbol AS symbol
      ON symbol.provider = bar.provider
     AND symbol.symbol = bar.symbol
    WHERE bar.bar_at >= bounds.window_start
      AND bar.bar_at + interval '1 minute' <= bounds.as_of_at
      AND symbol.country = 'US'
      AND symbol.kind = ANY(%(kinds)s)
    ORDER BY bar.provider, bar.symbol, bar.bar_at DESC
)
SELECT provider,
       symbol,
       label,
       kind,
       close,
       previous_close,
       bar_at
FROM closing
ORDER BY kind, symbol
