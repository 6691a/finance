-- 매매 신호 사건마다 그 뒤 N거래일의 누적 등락률. **조건부 기저율의 원재료다.**
--
-- 프롬프트가 "골든크로스가 곧 상승이 아니다, 같은 사건이 과거에 얼마나 맞았는지는 너도
-- 시스템도 아직 모른다"라고 적어 둔 자리를 이 값이 채운다
-- (docs/analysis/market-thesis/10-base-rate.md 5절).
--
-- **T+0은 없다.** 신호는 그날 종가로 검출되므로 T+0 등락률은 정의상 0이다. 채점 지평
-- (`THESIS_HORIZON_DAYS`)에는 0이 있지만 여기서는 셀 것이 없다.
--
-- **cutoff는 `created_at`이 아니라 `business_date`다.** 백필한 봉은 `created_at`이 전부
-- 백필 시각이라 `created_at`으로 걸면 과거 시점 재현이 통째로 0건이 된다. 기저율은
-- event-time 재현이 아니라 과거 통계라 거래일로 거는 것이 맞다.
--
-- look-ahead는 `signal_date`가 아니라 **결과가 난 날**로 막는다. 아직 지평만큼 지나지
-- 않은 사건은 표본이 아니다 — `horizon_date <= as_of_date`가 그 조건이다.
--
-- `rule_version`을 건다. 검출 규칙이 바뀌면 옛 사건은 다른 정의의 사건이다.
--
-- 분류(up/flat/down)는 여기서 하지 않는다. `thesis_domain.classify_outcome`이 채점과 같은
-- 임계로 파이썬에서 한다 — 임계를 당길 때 기저율과 채점이 함께 따라가야 하고, 경계값을
-- DB 없이 테스트해야 한다.
--
-- 주석에 퍼센트 기호를 쓰지 않는다. psycopg가 주석까지 훑어 플레이스홀더로 센다.
WITH bars AS (
    SELECT daily.provider,
           daily.symbol,
           daily.business_date,
           daily.close,
           row_number() OVER (
               PARTITION BY daily.provider, daily.symbol
               ORDER BY daily.business_date
           ) AS position
    FROM quote_daily AS daily
    JOIN quote_symbol AS symbol
      ON symbol.provider = daily.provider
     AND symbol.symbol = daily.symbol
    WHERE daily.business_date <= %(as_of_date)s
      AND daily.close > 0
      AND NOT (daily.provider = 'kis' AND symbol.kind = 'equity')

    UNION ALL

    SELECT daily.provider,
           daily.stock_code AS symbol,
           daily.business_date,
           daily.close_price AS close,
           row_number() OVER (
               PARTITION BY daily.provider, daily.stock_code
               ORDER BY daily.business_date
           ) AS position
    FROM stock_investor_trade_daily AS daily
    WHERE daily.business_date <= %(as_of_date)s
      AND daily.close_price > 0
      AND daily.provider = 'kis'
), horizons AS (
    SELECT unnest(%(horizons)s::int[]) AS horizon_days
), events AS (
    SELECT signal.id AS signal_id,
           signal.provider,
           signal.symbol,
           signal.signal_date,
           signal.kind,
           signal.direction,
           bars.position
    FROM technical_signal AS signal
    JOIN bars
      ON bars.provider = signal.provider
     AND bars.symbol = signal.symbol
     AND bars.business_date = signal.signal_date
    WHERE signal.rule_version = %(rule_version)s
)
SELECT events.symbol,
       events.kind,
       events.direction,
       events.signal_date,
       horizons.horizon_days,
       -- 신호일 종가 대비 지평 종가의 누적 등락률(퍼센트).
       round((future.close - base.close) / base.close * 100, 4) AS return_pct
FROM events
CROSS JOIN horizons
JOIN bars AS base
  ON base.provider = events.provider
 AND base.symbol = events.symbol
 AND base.position = events.position
JOIN bars AS future
  ON future.provider = events.provider
 AND future.symbol = events.symbol
 AND future.position = events.position + horizons.horizon_days
WHERE future.business_date <= %(as_of_date)s
ORDER BY events.symbol, events.kind, events.direction, horizons.horizon_days, events.signal_date
