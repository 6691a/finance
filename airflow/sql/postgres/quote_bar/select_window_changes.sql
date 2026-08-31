-- 추론 툴 `macro_changes`가 읽는 분석 창의 심볼별 변화. 뷰는 읽기 전용이라 조회만 한다.
--
-- 창의 첫 봉 종가와 마지막 봉 종가를 준다. 창을 슬롯이 정하므로(장전은 전 개장일 15:30부터,
-- 장후는 당일 09:00부터) 그 구간의 양 끝이 필요하다.
--
-- **마지막 봉의 `previous_close`도 함께 준다.** 그것은 직전 정규장 종가라 창과 구간이
-- 다르지만, 추론과 채점의 기준가가 전일 종가라 축이 하나 더 있어야 모델이 같은 축으로
-- 읽는다. 창 변화만 주면 개장 갭이 빠진 값을 하루 등락으로 읽는다. 두 축은 이름으로
-- 가른다 — 창은 first/last, 전일 종가 대비는 previous_close다.
--
-- **`bar_at + interval '1 minute' <= as_of_at`이다.** `bar_at`은 봉의 시작 시각이라
-- `bar_at <= as_of_at`으로 자르면 경계 봉이 담은 미래 1분이 섞인다.
--
-- 변화를 퍼센트로 만들지 않는다. 금리(`rate`)는 4.65에서 4.70으로 가는 것이 1.08퍼센트가
-- 아니라 5bp라, 퍼센트로 주면 모델이 급등으로 읽는다(`briefing/market.py`의 `QUOTED_KINDS`와
-- 같은 이유). 종가 둘과 `kind`를 주고 표기는 `modules/thesis/toolbox.py`가 정한다.
--
-- **주석에는 퍼센트 기호를 아예 쓰지 않는다.** psycopg는 주석까지 훑어 플레이스홀더를
-- 센다. 주석 안의 것도 자리 수에 들어가 실행이 거절된다(2026-08-21 실측).
--
-- `kind` 목록은 파라미터다. 개별 종목(`equity`)은 여기서 빼고 세션 등락률 SQL이 따로 준다.
--
-- **국내 지수는 뺀다.** 장중·장후 슬롯의 창이 당일 09:00부터라 국내 정규장의 개장 갭이
-- 창 밖으로 통째로 빠진다. 2026-08-27 실측: 코스피 창 변화가 마이너스 1.15인데 전일 종가
-- 대비는 플러스 1.53이었다 — 부호가 뒤집혔고 그 값이 근거 줄에 그대로 찍혔다. 국내 지수는
-- 관측 상태가 전일 종가 기준으로 이미 준다.
--
-- **국가만으로 거르면 틀린다.** 원/달러와 엔/원의 country가 KR이고 그쪽은 24시간 호가라
-- 창 변화가 뜻을 갖는다. 국내 지수선물도 야간 세션이 09:00 개장을 이어 줘서 갭이 없다
-- (2026-08-28 확인: KOSPI200 선물 봉이 하루 24시간 연속). 그래서 국가와 종류를 함께 건다.
--
-- **파라미터가 이름 방식이다.** 술어가 늘면서 위치 방식으로는 어느 자리가 무엇인지
-- 읽히지 않는다. psycopg는 한 문장에서 위치와 이름을 섞지 못하므로 전부 바꾼다
-- (같은 디렉터리 `select_thesis_us_close.sql`과 같은 형태다).
WITH bounds AS (
    SELECT %(window_start)s::timestamptz AS window_start,
           %(as_of_at)s::timestamptz AS as_of_at
),
windowed AS (
    SELECT bar.provider,
           bar.symbol,
           (array_agg(bar.close ORDER BY bar.bar_at))[1] AS first_close,
           (array_agg(bar.close ORDER BY bar.bar_at DESC))[1] AS last_close,
           (array_agg(bar.previous_close ORDER BY bar.bar_at DESC))[1] AS last_previous_close,
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
       windowed.bar_count,
       windowed.last_previous_close
FROM windowed
JOIN quote_symbol AS symbol
  ON symbol.provider = windowed.provider
 AND symbol.symbol = windowed.symbol
WHERE symbol.kind = ANY(%(kinds)s)
  AND NOT (symbol.country = %(domestic_country)s AND symbol.kind = ANY(%(domestic_kinds)s))
ORDER BY symbol.kind, windowed.symbol
