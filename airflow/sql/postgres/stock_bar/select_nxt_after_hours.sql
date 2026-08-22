-- 장후 NXT 리뷰(`post_nxt_close`)의 관측 상태. 종목마다 애프터마켓 마지막 봉 하나.
--
-- **애프터마켓만이다.** NXT는 프리(08:00~08:50)와 주간(09:00~15:20)도 체결하므로
-- `exchange = 'NXT'`만으로 거르면 하루 전체가 섞인다 -- 2026-08-21 운영 DB 실측으로
-- 하루 690봉 중 애프터는 260봉(15:40~19:59)뿐이다. 창의 양 끝을 파라미터로 받는다 --
-- 세션 날짜에서 KST 경계를 만드는 일은 파이썬이 한다(`index_bar/select_session_return.sql`
-- 계보). SQL에 시간대 변환을 넣으면 컨테이너 설정에 따라 조용히 달라진다.
--
-- **분모가 `stock_bar.previous_close`가 아니다.** 그 칸은 전일 KRX 확정 종가라(스키마 주석)
-- 정규장 등락률의 분모다. 애프터 등락은 **당일 15:30 확정 종가** 대비로 재야 하므로
-- `stock_investor_trade_daily.close_price`를 조인한다. 그 값은 `kis_investor_trade_daily`가
-- KST 18:10에 넣는 `stck_clpr`이고 채점이 보는 것과 같은 원본이다. 여기를 틀리면 애프터에서
-- 0.7 움직인 것이 하루 등락으로 조용히 부풀려진다.
--
-- 확정 종가가 아직 없으면 등락률이 NULL이다. 부르는 쪽의 readiness guard가 먼저 막지만
-- 0으로 꾸미지는 않는다.
--
-- **`bar_count`와 `all_final`을 함께 준다.** 봉이 0개면 애프터마켓 거래가 없었던 것이고
-- (부르는 쪽이 skip), 전부 `is_final = false`면 20:05 REST 백필이 아직 안 돈 것이다
-- (부르는 쪽이 재시도). 잠정 봉 위에 추론을 쓰면 첫 성공본 불변 때문에 영영 못 고친다.
--
-- 창 상한은 `bar_at <= window_end`다. `quote_bar/select_window_changes.sql`의
-- `bar_at + interval '1 minute' <= as_of_at` 규칙을 쓰지 않는 것은 그 규칙이 창 도중을
-- 자를 때 경계 봉이 담은 미래 1분을 빼는 것이기 때문이다. 여기 20:00 봉은 NXT 마감
-- 체결이라 세션의 일부이고, 배제하면 세션의 끝을 못 본다.
WITH bounds AS (
    SELECT %s::timestamptz AS window_start,
           %s::timestamptz AS window_end,
           %s::date AS business_date
),
after_hours AS (
    SELECT bar.stock_code,
           bar.bar_at,
           bar.close,
           ROW_NUMBER() OVER (PARTITION BY bar.stock_code ORDER BY bar.bar_at DESC) AS recency,
           count(*) OVER (PARTITION BY bar.stock_code) AS bar_count,
           bool_and(bar.is_final) OVER (PARTITION BY bar.stock_code) AS all_final
    FROM stock_bar AS bar
    CROSS JOIN bounds
    WHERE bar.provider = 'kis'
      AND bar.exchange = 'NXT'
      AND bar.stock_code = ANY(%s)
      AND bar.bar_at >= bounds.window_start
      AND bar.bar_at <= bounds.window_end
)
SELECT after_hours.stock_code,
       after_hours.bar_at AS last_bar_at,
       after_hours.close AS last_close,
       after_hours.bar_count,
       after_hours.all_final,
       settled.close_price AS settled_close,
       CASE
           WHEN settled.close_price IS NULL OR settled.close_price = 0 THEN NULL
           ELSE (after_hours.close - settled.close_price) / settled.close_price * 100
       END AS return_pct
FROM after_hours
CROSS JOIN bounds
LEFT JOIN stock_investor_trade_daily AS settled
       ON settled.provider = 'kis'
      AND settled.stock_code = after_hours.stock_code
      AND settled.business_date = bounds.business_date
WHERE after_hours.recency = 1
ORDER BY after_hours.stock_code
