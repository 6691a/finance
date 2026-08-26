-- Slack 브리핑이 심볼마다 마지막 봉 하나를 읽는다.
-- 등락은 봉에 이미 있는 previous_close로 계산하므로 전일 세션을 따로 찾지 않는다.
-- 나라·종류로 거르지 않고 전부 받아 파이썬이 리포트별로 나눈다. 심볼 수가 수십 개라
-- 쿼리를 리포트마다 나누는 값어치가 없다.
--
-- session_open은 그 세션의 첫 봉 시가다. 정규장 발송이 전일 종가 대비와 함께 그린다.
--
-- **세션 경계는 KST 08:00이다.** 자정이 아니다 — 국내 NXT 프리마켓(08:00)과 CME 야간 세션
-- (미국 동부 18:00 = KST 08:00)이 둘 다 그 시각에 시작한다. 자정으로 자르면 KST 새벽까지
-- 이어지는 미국 선물 세션이 두 동강 나서 "시가"가 세션 한가운데 값이 된다.
--
-- **previous_close로 세션을 가르지 않는다.** 봉마다 같은 값이 실려 오지만 전일 종가가
-- 이틀 연속 같으면 두 세션이 한 덩이가 된다. 2026-08-26 실측에서 005930의 08-25·08-26
-- previous_close가 둘 다 257,000이었고, 그때 시가로 전날 오후 봉이 잡혔다.
--
-- 그 경계 안의 **첫 봉**이 시가다. 국내 지수선물처럼 단일가(08:30~09:00) 봉이 쌓이는
-- 심볼은 그 봉이 시가로 잡힌다 — 실제로 체결된 값이라 지어낸 값이 아니지만 KRX가 고시하는
-- 09:00 시가와는 다를 수 있다(2026-08-26 실측: A01609가 08:45 봉 1,070.50).
--
-- **상관 LATERAL로 심볼마다 되짚지 않는다.** 실측에서 60초를 넘겼다(quote_bar는 일곱 테이블
-- UNION 뷰다). 어차피 같은 창을 한 번 훑으므로 세션 첫 봉을 CTE로 한 번에 모아 조인한다.
WITH session_opens AS (
    SELECT DISTINCT ON (windowed.provider, windowed.symbol, windowed.session_day)
           windowed.provider,
           windowed.symbol,
           windowed.session_day,
           windowed.open
    FROM (
        SELECT provider,
               symbol,
               bar_at,
               open,
               (((bar_at AT TIME ZONE 'Asia/Seoul') - INTERVAL '8 hours')::date) AS session_day
        FROM quote_bar
        WHERE bar_at >= %s
    ) AS windowed
    ORDER BY windowed.provider, windowed.symbol, windowed.session_day, windowed.bar_at
)
SELECT DISTINCT ON (bar.provider, bar.symbol)
       bar.provider,
       bar.symbol,
       symbol.label,
       symbol.kind,
       symbol.country,
       bar.close,
       bar.previous_close,
       bar.bar_at,
       session.open AS session_open
FROM quote_bar AS bar
JOIN quote_symbol AS symbol
  ON symbol.provider = bar.provider
 AND symbol.symbol = bar.symbol
LEFT JOIN session_opens AS session
  ON session.provider = bar.provider
 AND session.symbol = bar.symbol
 AND session.session_day = (((bar.bar_at AT TIME ZONE 'Asia/Seoul') - INTERVAL '8 hours')::date)
WHERE bar.bar_at >= %s
ORDER BY bar.provider, bar.symbol, bar.bar_at DESC
