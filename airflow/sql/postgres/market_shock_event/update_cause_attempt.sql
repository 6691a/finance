-- 시도 횟수를 올리고, 없으면 기한도 채운다. **모델을 부르기 전에 커밋한다.**
--
-- 부르고 나서 올리면 죽은 실행이 안 세어져 "안 돌았다"와 "돌다 죽었다"를 못 가른다.
-- 별도 원장 표를 안 두는 대신 이 칸이 그 일을 한다.
--
-- `cause_deadline`은 `coalesce`라 이미 있으면 안 건드린다. 포착 시각에 달력이 없어
-- NULL로 남았던 것만 여기서 채워진다.
UPDATE market_shock_event
SET cause_attempts = cause_attempts + 1,
    cause_deadline = coalesce(cause_deadline, %(deadline)s)
WHERE id = %(id)s
RETURNING cause_attempts, cause_deadline
