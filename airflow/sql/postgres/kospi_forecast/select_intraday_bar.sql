-- 장중 슬롯의 현재가. 기준 시각 **직전** 봉 하나와 그날 고가·저가·시가.
--
-- `bar_at = as_of_at`으로 딱 집지 않는다. 봉 시각은 구간의 시작이라 11:35 시점의 11:35 봉은
-- 아직 안 끝났고, `kis_quote_intraday`가 `*/5`로 돌아 수집이 한 주기 밀릴 수도 있다.
--
-- **하한(개장 시각)을 받는 이유는 전일 봉이 딸려 오는 것을 막기 위해서다.** 하한이 없으면
-- 수집이 통째로 죽은 날에도 어제 15:30 봉이 "지금 가격"으로 실린다. 0건은 실패로 다룬다.
--
-- `is_final`은 보지 않는다. 장중 봉은 잠정이 정상이다.
--
-- `previous_close`는 직전 거래일 확정 종가다. "오늘 여기까지 얼마나 왔나"의 분모이고
-- 예측 기준가(`close`)와 축이 다르다.
WITH session_bars AS (
    SELECT bar_at, open, high, low, close, previous_close
    FROM index_bar
    WHERE provider = %(provider)s
      AND symbol = %(symbol)s
      AND bar_at < %(as_of_at)s
      AND bar_at >= %(session_start)s
)
SELECT latest.bar_at,
       latest.close,
       latest.previous_close,
       (SELECT open FROM session_bars ORDER BY bar_at LIMIT 1) AS session_open,
       (SELECT max(high) FROM session_bars) AS session_high,
       (SELECT min(low) FROM session_bars) AS session_low
FROM session_bars AS latest
ORDER BY latest.bar_at DESC
LIMIT 1
