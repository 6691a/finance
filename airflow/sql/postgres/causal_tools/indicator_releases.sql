-- 인과 그래프 툴 `macro_indicators`가 쓴다. **그 창에 고시된 지표**와 직전 관측 대비 변화다.
--
-- 추론의 `select_thesis_latest.sql`과 목적이 다르다. 저쪽은 "지금 매크로가 어느 수준인가"라
-- 언제나 최신 한 건이면 되지만, 이 그래프는 되짚기라 **"그 창에 무엇이 발표됐나"**가
-- 사건이 된다. 언제나 최신을 주면 대상 주에 아무 발표가 없었던 것과 그 주에 CPI가 나온
-- 것을 구분하지 못한다.
--
-- **계열마다 창 안 마지막 한 건만 준다.** 국채와 정책금리는 매일 고시라 그대로 펴면 변화
-- 없는 행이 상한을 다 먹는다(2026-08-28 실측: 2주 창에 `policy_rate` 40행이 전부 동결
-- 행이었고 독일 국채도 하루 0.00~0.01 움직임이었다). 접으면 계열 하나가 한 줄이라 같은
-- 예산에 kind 전체가 들어온다.
--
-- **변화는 창 기준이 아니라 직전 관측 대비다.** 계열마다 주기가 달라(일별·주별·월별) 창
-- 기준으로 접으면 월간 지표의 변화가 "2주간 변화"로 읽힌다. 전월 대비·전일 대비가 그
-- 계열의 표준이고 모델이 아는 뜻도 그쪽이다.
--
-- **전년 동월 값을 함께 준다**(2026-08-28). 직전 관측만으로는 물가·고용의 표준 독법인
-- 전년 대비를 못 낸다 — 프로토타입에서 모델이 CPI 지수 332.813을 받고도 `연율 3.4퍼센트`를
-- **기사 요약에서** 가져다 근거로 썼다. 그 숫자를 우리 데이터로 대조할 방법이 없었다.
--
-- 1년 전 값은 `LATERAL`로 찾는다. 계열마다 주기가 달라 `lag(12)` 같은 고정 칸수는 틀린다 —
-- 주간 계열이면 52칸이고 일별이면 250칸 안팎이다. **1년 전 이하 중 가장 가까운 관측**이
-- 어느 주기에서도 맞는 정의다. cutoff는 여기에도 건다.
--
-- **창은 `observation_date`, cutoff는 `created_at`이다.** 둘이 다른 값을 잰다 — 관측일은
-- 제공처의 기준일이고 우리가 언제 알았는지는 별개다. 근거는 "그 시점에 알 수 있었던 것"
-- 이어야 하므로(설계 §5.1) cutoff는 우리 쪽 시각으로 건다. 백필한 계열은 `created_at`이
-- 전부 최근이라 과거 주 실행에서 통째로 빠질 수 있는데, 그것이 사실이다 — 그 주에 우리는
-- 그 값을 갖고 있지 않았다(설계 §2.1과 같은 자리).
--
-- **직전 값은 창 밖에서 온다.** 월간 지표의 직전 값은 한 달 전이라 창 안에 없다. `lag`를
-- cutoff까지의 전체 이력 위에서 계산하고 나서 창으로 좁히는 순서가 그 때문이다. 반대로
-- 하면 그 창 첫 관측의 직전 값이 언제나 비어 "첫 발표"처럼 보인다.
--
-- **kind로 좁히는 것이 두 번째 핵심이다.** 한 테이블에 국채 금리(Percent)와 물가지수
-- (Index 1982-1984=100)와 소매판매(Thousand US Dollars)가 함께 있어 걸지 않으면 단위가
-- 다른 값이 한 표에 섞인다.
WITH visible AS (
    SELECT observation.provider,
           observation.series_id,
           observation.observation_date,
           observation.value,
           observation.unit,
           series.country,
           series.country_name,
           series.label,
           series.kind,
           lag(observation.value) OVER series_history AS previous_value,
           lag(observation.observation_date) OVER series_history AS previous_date,
           year_ago.value AS year_ago_value,
           year_ago.observation_date AS year_ago_date
    FROM indicator_observation AS observation
    JOIN indicator_series AS series
      ON series.provider = observation.provider
     AND series.series_id = observation.series_id
    LEFT JOIN LATERAL (
        SELECT past.value, past.observation_date
        FROM indicator_observation AS past
        WHERE past.provider = observation.provider
          AND past.series_id = observation.series_id
          AND past.observation_date <= observation.observation_date - INTERVAL '1 year'
          AND past.created_at <= %(as_of_at)s
          AND past.value IS NOT NULL
        ORDER BY past.observation_date DESC
        LIMIT 1
    ) AS year_ago ON TRUE
    WHERE series.kind = ANY(%(kinds)s)
      AND observation.value IS NOT NULL
      AND observation.observation_date <= %(end)s
      AND observation.created_at <= %(as_of_at)s
    WINDOW series_history AS (
        PARTITION BY observation.provider, observation.series_id
        ORDER BY observation.observation_date
    )
),
in_window AS (
    SELECT visible.*,
           row_number() OVER (
               PARTITION BY provider, series_id
               ORDER BY observation_date DESC
           ) AS recency
    FROM visible
    WHERE observation_date >= %(start)s
)
SELECT provider,
       series_id,
       country,
       country_name,
       label,
       kind,
       unit,
       observation_date,
       value,
       previous_date,
       previous_value,
       year_ago_date,
       year_ago_value
FROM in_window
WHERE recency = 1
ORDER BY observation_date DESC, kind, country, series_id
LIMIT %(limit)s
