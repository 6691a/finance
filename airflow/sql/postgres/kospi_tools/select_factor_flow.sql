-- `factor_history` 툴이 시장 단위 투자자 수급을 읽는다. 날마다 그날 마지막 스냅샷 하나.
--
-- **확정 일별 표가 없다.** 이 테이블은 장중 누적치를 분 단위로 쌓고, 시장 단위로 확정된
-- 일별 행을 주는 표가 저장소에 없다(2026-09-02 확인). 그래서 날마다 마지막 스냅샷을 그날
-- 값으로 쓴다. 확정치와 얼마나 다른지는 아직 재지 않았다 — 설계 §9의 첫 항목이다.
--
-- **수량을 준다.** `*_net_buy_amount`는 모델 주석이 "단위 미확정"이라 그 값을 원이라고
-- 부르면 거짓이 된다. 금액도 함께 주되 단위를 밝히지 않는다.
--
-- 오늘 행은 아직 진행 중이라 마지막 스냅샷이 곧 "그 시각까지"다. 그것이 장중 슬롯이
-- 원하는 값이라 따로 가르지 않는다.
--
-- 창의 끝은 `observed_at`이다. 장중 누적치라 관측 시각이 곧 event time이다.
WITH daily AS (
    SELECT DISTINCT ON ((observed_at AT TIME ZONE 'Asia/Seoul')::date)
           (observed_at AT TIME ZONE 'Asia/Seoul')::date AS business_date,
           observed_at,
           foreign_net_buy_qty,
           institution_net_buy_qty,
           individual_net_buy_qty,
           foreign_net_buy_amount,
           institution_net_buy_amount,
           individual_net_buy_amount
    FROM market_investor_flow_snapshot
    WHERE market_code = %(market_code)s
      AND observed_at <= %(as_of_at)s
    ORDER BY (observed_at AT TIME ZONE 'Asia/Seoul')::date DESC, observed_at DESC
)
SELECT business_date,
       observed_at,
       foreign_net_buy_qty,
       institution_net_buy_qty,
       individual_net_buy_qty,
       foreign_net_buy_amount,
       institution_net_buy_amount,
       individual_net_buy_amount
FROM (SELECT * FROM daily ORDER BY business_date DESC LIMIT %(limit)s) AS window_rows
ORDER BY business_date
