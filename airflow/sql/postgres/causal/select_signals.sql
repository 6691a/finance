-- 그 주 대상의 기술적 매매 신호 전량.
--
-- 지표값(SMA·RSI 자체)은 문맥이라 인용 대상이 아니지만 **신호는 사건이라 인용할 수 있다**.
-- `thesis_evidence`가 `technical_signal`을 근거 종류로 두는 것과 같은 판단이다.
--
-- 신호를 갖는 대상은 국내 지수 둘과 종목 둘뿐이다(2026-08-27 실측: 전체 1,025건이
-- KOSPI·KOSDAQ·005930·000660에 나뉜다). 매크로·금리는 여기 안 걸린다.
--
-- 한 주에 몇 건이라 상위로 안 좁힌다. 없는 주도 흔하다 — 8주 프로토타입의 대상 주에는
-- 한 건도 없었다.
--
-- `as_of_at` cutoff를 건다. 신호는 확정 일봉에서 계산되므로 늦게 들어올 수 있다.
SELECT id,
       symbol,
       signal_date,
       kind,
       direction
FROM technical_signal
WHERE symbol = ANY(%(codes)s)
  AND signal_date BETWEEN %(week_start)s AND %(week_end)s
  AND created_at < %(as_of_at)s
ORDER BY signal_date, symbol, id
