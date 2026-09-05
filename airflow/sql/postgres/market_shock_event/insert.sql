-- 포착 하나를 쓴다. **첫 성공본은 불변이다.**
--
-- 같은 `(symbol, detected_at)`에 행이 있으면 아무 것도 바꾸지 않는다. 재시도가 같은 창을
-- 다시 보고 같은 봉을 집으므로 그 경로에서만 충돌한다 — 그때 값을 덮어쓸 이유가 없다.
--
-- 충돌하면 `RETURNING`이 0행이다. 부르는 쪽이 그것으로 "새로 만들었나"를 가르고 Slack을
-- 보낼지 정한다. 재시도가 알림을 두 번 보내지 않는 것이 이 반환의 목적이다.
--
-- `cause_*`는 여기서 안 쓴다. `cause_status`가 기본값 `pending`으로 열리고 원인 DAG가
-- 나중에 채운다. `cause_deadline`만 포착 시점에 계산해 넣는다 — 달력을 아직 못 채웠으면
-- NULL이고 원인 DAG가 그때 다시 구한다.
INSERT INTO market_shock_event (
    symbol,
    session_date,
    direction,
    detected_at,
    window_start,
    window_end,
    extreme_at,
    extreme_price,
    trigger_price,
    move_pct,
    window_change_pct,
    bar_count,
    peers,
    threshold_pct,
    cause_deadline
) VALUES (
    %(symbol)s,
    %(session_date)s,
    %(direction)s,
    %(detected_at)s,
    %(window_start)s,
    %(window_end)s,
    %(extreme_at)s,
    %(extreme_price)s,
    %(trigger_price)s,
    %(move_pct)s,
    %(window_change_pct)s,
    %(bar_count)s,
    %(peers)s::jsonb,
    %(threshold_pct)s,
    %(cause_deadline)s
)
ON CONFLICT (symbol, detected_at) DO NOTHING
RETURNING id
