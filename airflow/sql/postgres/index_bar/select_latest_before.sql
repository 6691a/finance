-- 지수의 **기준 시각 직전 봉** 하나씩. 장중 추론의 현재가이자 예측 기준가다.
--
-- `bar_at = as_of_at`으로 딱 집지 않는다. 봉 시각은 구간의 시작이라 10:35 시점의 10:35
-- 봉은 아직 안 끝났고, 1분봉이지만 `kis_quote_intraday`가 `*/5`로 돌아 수집이 한 주기
-- 밀릴 수도 있다. 그래서 "그 시각 **앞의** 가장 최근 봉"을 고른다.
--
-- **하한을 받는 이유는 전일 봉이 딸려 오는 것을 막기 위해서다.** 하한 없이 최신 봉을
-- 고르면 수집이 통째로 죽은 날에도 어제 15:30 봉이 나와서 "지금 가격"으로 실린다.
-- 부르는 쪽은 그날 개장 시각(`thesis_common.open_at`)을 준다. 0건은 실패로 다뤄야 한다.
--
-- `is_final`은 보지 않는다. 장중 봉은 잠정이 정상이고 `kis_quote_intraday`가 다음 주기에
-- 다시 쓴다. 확정을 기다리면 장중 추론이 영영 서지 않는다.
--
-- `previous_close`는 직전 거래일 확정 종가다. "오늘 여기까지 얼마나 왔나"의 분모이고
-- 예측 기준가(`close`)와 축이 다르다.
SELECT DISTINCT ON (symbol)
       symbol,
       bar_at,
       close,
       previous_close
FROM index_bar
WHERE provider = 'kis'
  AND symbol = ANY(%s)
  AND bar_at < %s
  AND bar_at >= %s
ORDER BY symbol, bar_at DESC
