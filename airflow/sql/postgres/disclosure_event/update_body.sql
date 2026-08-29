-- 공시 본문을 채운다. 행은 목록 수집이 이미 만들었으므로 UPDATE다.
--
-- **`body IS NULL`을 조건에 둔다.** 같은 접수번호를 두 번 받아도 먼저 저장된 본문을 안 덮는다.
-- 원문이 바뀌면 DART는 새 접수번호로 정정 공시를 내므로, 같은 번호의 내용이 달라지는 것은
-- 우리 파싱이 바뀐 경우뿐이고 그때는 사람이 판단할 일이다.
UPDATE disclosure_event
SET body = %(body)s,
    updated_at = now()
WHERE provider = 'dart'
  AND rcept_no = %(rcept_no)s
  AND body IS NULL
