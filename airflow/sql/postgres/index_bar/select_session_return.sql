-- 지수의 한 세션 등락률. 추론의 관측 상태와 채점이 같은 원본을 읽는다.
--
-- 마감 봉(15:30 KST 시작)의 종가와 그 봉이 들고 있는 `previous_close`를 쓴다.
-- `kis_quote_intraday`가 `*/5 8-16`으로 돌아 16:00이면 확정이다.
--
-- **봉 시각은 파라미터로 받는다.** 세션 날짜에서 마감 봉 시각(UTC)을 만드는 일은 KST
-- 경계 계산이라 파이썬이 한다(`modules/period.py` 계보). SQL에 시간대 변환을 넣으면
-- 컨테이너 설정에 따라 조용히 달라진다.
--
-- 봉이 없으면 그 지수는 결과에 없다. 부르는 쪽은 미채점으로 남긴다.
SELECT symbol,
       bar_at,
       close,
       previous_close,
       CASE
           WHEN previous_close = 0 THEN NULL
           ELSE (close - previous_close) / previous_close * 100
       END AS return_pct
FROM index_bar
WHERE provider = 'kis'
  AND bar_at = %s
  AND symbol = ANY(%s)
ORDER BY symbol
