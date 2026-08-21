-- 추론 툴 `macro_changes`가 읽는 분석 창의 심볼별 변화. 뷰는 읽기 전용이라 조회만 한다.
--
-- 창의 첫 봉 종가와 마지막 봉 종가를 준다. `previous_close`를 쓰지 않는 이유는 그것이
-- 직전 정규장 종가라서 "밤사이 얼마나 움직였나"와 구간이 다르기 때문이다. 창을 슬롯이
-- 정하므로(장전은 전 개장일 15:30부터, 장후는 당일 09:00부터) 그 구간의 양 끝이 필요하다.
--
-- **`bar_at + interval '1 minute' <= as_of_at`이다.** `bar_at`은 봉의 시작 시각이라
-- `bar_at <= as_of_at`으로 자르면 경계 봉이 담은 미래 1분이 섞인다.
--
-- 변화를 퍼센트로 만들지 않는다. 금리(`rate`)는 4.65에서 4.70으로 가는 것이 1.08퍼센트가
-- 아니라 5bp라, 퍼센트로 주면 모델이 급등으로 읽는다(`briefing/market.py`의 `QUOTED_KINDS`와
-- 같은 이유). 종가 둘과 `kind`를 주고 표기는 `modules/thesis.py`가 정한다.
--
-- **주석에는 퍼센트 기호를 아예 쓰지 않는다.** psycopg는 주석까지 훑어 플레이스홀더를
-- 센다. 주석 안의 것도 자리 수에 들어가 실행이 거절된다(2026-08-21 실측).
--
-- `kind` 목록은 파라미터다. 개별 종목(`equity`)은 여기서 빼고 세션 등락률 SQL이 따로 준다.
WITH bounds AS (
    SELECT %s::timestamptz AS window_start,
           %s::timestamptz AS as_of_at
),
windowed AS (
    SELECT bar.provider,
           bar.symbol,
           (array_agg(bar.close ORDER BY bar.bar_at))[1] AS first_close,
           (array_agg(bar.close ORDER BY bar.bar_at DESC))[1] AS last_close,
           min(bar.bar_at) AS first_bar_at,
           max(bar.bar_at) AS last_bar_at,
           count(*) AS bar_count
    FROM quote_bar AS bar
    CROSS JOIN bounds
    WHERE bar.bar_at >= bounds.window_start
      AND bar.bar_at + interval '1 minute' <= bounds.as_of_at
    GROUP BY bar.provider, bar.symbol
)
SELECT windowed.provider,
       windowed.symbol,
       symbol.label,
       symbol.kind,
       symbol.country,
       windowed.first_close,
       windowed.last_close,
       windowed.first_bar_at,
       windowed.last_bar_at,
       windowed.bar_count
FROM windowed
JOIN quote_symbol AS symbol
  ON symbol.provider = windowed.provider
 AND symbol.symbol = windowed.symbol
WHERE symbol.kind = ANY(%s)
ORDER BY symbol.kind, windowed.symbol
