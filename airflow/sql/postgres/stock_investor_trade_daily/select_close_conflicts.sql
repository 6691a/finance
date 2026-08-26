-- 들어온 확정 일봉과 이미 저장된 행의 종가가 어긋나는 거래일. **소급 조정 감지다.**
--
-- KIS는 수정주가를 준다. 액면분할·병합·증자가 있으면 그날 이후로 **과거 전체가 새 기준으로
-- 다시 쓰인다**(2026-08-26 실측: 2018-05-04 분할 뒤 2010-01-04 종가가 809,000이 아니라
-- 16,180으로 온다. 가격 나누기 50, 수량 곱하기 50, 금액 불변). `FID_ORG_ADJ_PRC`로 원주가를
-- 고를 수 없다 — 빈 값, 0, 1이 전부 같은 답을 준다.
--
-- 그래서 DB는 **받은 그 순간의 기준**으로 얼어 있고, 조정 뒤에 겹치는 30 거래일만 upsert되면
-- 한 종목 안에 두 기준이 섞인다. SMA60이 60봉 중 일부만 자릿수가 다른 값으로 계산된다.
--
-- **세로가 아니라 가로로 본다.** 날짜 간 급락은 진짜 폭락일 수 있어 조정과 가를 수 없다.
-- 여기서 보는 것은 **같은 거래일의 두 값**이다 — 과거는 변하지 않으므로 한 날짜의 종가가
-- 둘일 수 없고, 어긋났다면 소급 조정 말고는 설명이 없다. 그래서 오탐이 없다.
--
-- 종가 하나만 본다. 조정은 OHLC와 수량을 전부 바꾸므로 하나로 충분하다.
-- 비교는 numeric끼리라 자릿수 표기 차이(53000 대 53000.00)에 걸리지 않는다.
--
-- 판단은 DAG가 한다. 여기는 어긋난 거래일만 돌려준다.
SELECT stored.business_date
FROM stock_investor_trade_daily AS stored
JOIN unnest(%s::date[], %s::numeric[]) AS incoming(business_date, close_price)
  ON incoming.business_date = stored.business_date
WHERE stored.provider = 'kis'
  AND stored.stock_code = %s
  AND stored.close_price IS DISTINCT FROM incoming.close_price
ORDER BY stored.business_date
