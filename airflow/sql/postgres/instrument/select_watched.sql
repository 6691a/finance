-- 프롬프트에 넣을 종목 후보. 마스터가 허용 값을 정한다.
-- 자유 문자열 태그를 받으면 document_instrument가 instrument와 조인되지 않는다.
SELECT ticker, name
FROM instrument
WHERE is_watched
ORDER BY ticker
