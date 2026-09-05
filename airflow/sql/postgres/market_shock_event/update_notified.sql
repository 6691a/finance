-- 포착 Slack을 보낸 시각을 찍는다.
--
-- 저장과 발송을 나눈 자리다. 발송이 실패하면 `notified_at`이 NULL로 남고, 그 사실이
-- "저장은 됐는데 아무도 못 봤다"를 말한다. 저장 트랜잭션 안에서 Slack을 부르면 발송
-- 실패가 사건 기록까지 되돌린다.
UPDATE market_shock_event
SET notified_at = %(notified_at)s
WHERE id = %(id)s
