-- 같은 창의 다른 시장들. **한 번에 부른다.**
--
-- 심볼마다 따로 부르면 여섯 왕복이 되고, 그 사이에 새 봉이 들어와 시장마다 다른 창을
-- 보게 된다. 창이 어긋나면 "같이 움직였나"라는 질문 자체가 무의미해진다.
--
-- **표가 둘이다.** 아시아 지수는 `index_bar`(KIS), 미국 선물은 `index_future_bar`(Yahoo)에
-- 있다. 한국 장중에 미국 현물장은 닫혀 있어 현물 봉이 없고, 그 시간에 움직이는 것이
-- 지수선물이다. 그래서 UNION이지 조인이 아니다 — 두 표는 서로를 모른다.
--
-- 심볼 이름이 두 표에서 겹치지 않아(`_FUT` 접미) 구분 컬럼을 두지 않는다. 겹치는 이름을
-- 나중에 더하면 여기부터 고친다.
--
-- **봉이 0건인 시장은 이 결과에 아예 안 나온다.** 부르는 쪽이 요청한 심볼 목록과 대조해
-- `available=false`로 채운다 — 여기서 빈 행을 만들지 않는다.
SELECT symbol, bar_at, open, high, low, close
FROM index_bar
WHERE provider = %(index_provider)s
  AND symbol = ANY(%(index_symbols)s)
  AND bar_at >= %(window_start)s
  AND bar_at < %(window_end)s

UNION ALL

SELECT symbol, bar_at, open, high, low, close
FROM index_future_bar
WHERE provider = %(future_provider)s
  AND symbol = ANY(%(future_symbols)s)
  AND bar_at >= %(window_start)s
  AND bar_at < %(window_end)s

ORDER BY symbol, bar_at
