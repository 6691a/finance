-- Slack 브리핑이 국내 종목의 마지막 봉 하나를 읽는다. quote_bar 뷰는 NXT를 태우지
-- 않으므로(거래소가 섞임) 물리 테이블을 직접 본다.
-- KRX·NXT 중 최신 봉을 고르되 같은 분이면 KRX를 우선한다. 정규장(09:00~15:30)은 KRX가,
-- 이후(NXT 애프터마켓 ~20:00)는 NXT가 자연히 이긴다. NXT 봉의 previous_close도 KRX 확정
-- 종가라(스키마 주석) 등락률 분모가 거래소와 무관하게 같다.
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
-- **상관 LATERAL로 심볼마다 되짚지 않는다.** 실측에서 60초를 넘겼다(quote_bar는 일곱 테이블
-- UNION 뷰다). 어차피 같은 창을 한 번 훑으므로 세션 첫 봉을 CTE로 한 번에 모아 조인한다.
--
-- **시가는 KRX 봉에서만 뽑는다.** 안 걸면 NXT 프리마켓(08:00~08:50) 첫 봉이 "시가"가 되어
-- 증권사 앱이 말하는 시가와 다른 값이 나온다. KRX 09:00 시가는 애프터마켓 시간대에도
-- 그날의 기준값으로 맞다.
WITH session_opens AS (
    SELECT DISTINCT ON (windowed.provider, windowed.stock_code, windowed.session_day)
           windowed.provider,
           windowed.stock_code,
           windowed.session_day,
           windowed.open
    FROM (
        SELECT provider,
               stock_code,
               bar_at,
               open,
               (((bar_at AT TIME ZONE 'Asia/Seoul') - INTERVAL '8 hours')::date) AS session_day
        FROM stock_bar
        WHERE bar_at >= %s
          AND exchange = 'KRX'
    ) AS windowed
    ORDER BY windowed.provider, windowed.stock_code, windowed.session_day, windowed.bar_at
)
SELECT DISTINCT ON (bar.provider, bar.stock_code)
       bar.provider,
       bar.stock_code,
       symbol.label,
       symbol.kind,
       symbol.country,
       bar.close,
       bar.previous_close,
       bar.bar_at,
       bar.exchange,
       session.open AS session_open
FROM stock_bar AS bar
JOIN quote_symbol AS symbol
  ON symbol.provider = bar.provider
 AND symbol.symbol = bar.stock_code
LEFT JOIN session_opens AS session
  ON session.provider = bar.provider
 AND session.stock_code = bar.stock_code
 AND session.session_day = (((bar.bar_at AT TIME ZONE 'Asia/Seoul') - INTERVAL '8 hours')::date)
WHERE bar.bar_at >= %s
  AND bar.exchange IN ('KRX', 'NXT')
ORDER BY bar.provider, bar.stock_code, bar.bar_at DESC, (bar.exchange = 'KRX') DESC
