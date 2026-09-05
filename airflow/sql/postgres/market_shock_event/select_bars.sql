-- 급변 판정 대상의 창 봉. 코스피 하나를 부른다.
--
-- **창의 양 끝을 부르는 쪽이 준다.** 여기서 `now()`를 쓰지 않는다 — 창의 끝이 실행 시각이
-- 아니라 그보다 `LAG_MINUTES`만큼 앞이고, 그 값이 손잡이라 SQL에 박으면 못 옮긴다.
--
-- 끝은 열린 구간이다(`< window_end`). 봉 시각이 구간의 시작이라 창 끝과 같은 봉은 아직
-- 안 끝났다.
--
-- `is_final`은 보지 않는다. 장중 봉은 잠정이 정상이다.
SELECT bar_at, open, high, low, close
FROM index_bar
WHERE provider = %(provider)s
  AND symbol = %(symbol)s
  AND bar_at >= %(window_start)s
  AND bar_at < %(window_end)s
ORDER BY bar_at
