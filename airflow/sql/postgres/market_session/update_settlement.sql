-- KIS 해외결제일자조회가 미국 행의 결제일만 채운다.
--
-- **개장 판정(effective_open_day, verified_by, verified_at)을 건드리지 않는다.** 그 값은
-- NYSE가 소유한다. 행이 없으면 만들지 않는다. NYSE가 지원 연도 전체를 이미 만들어 두므로
-- 행이 없다는 것은 지원 연도 밖이라는 뜻이다.
--
-- RETURNING은 갱신된 행의 판정을 돌려준다. 미국 행이 응답에 없는데 NYSE는 개장으로 본
-- 날짜를 경고로 남기는 데 쓴다.
UPDATE market_session SET
    local_settlement_date = %s,
    domestic_settlement_date = %s,
    verification_source_record_id = %s,
    updated_at = now()
WHERE market_code = 'US_EQUITY'
  AND session_date = %s
RETURNING effective_open_day
