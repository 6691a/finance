-- 문서에서 알아볼 종목 후보. **마스터 전체다** — `is_watched`를 거르지 않는다.
--
-- **시세가 없어도 성립하는 소비자만 이 파일을 읽는다.** 지금은 둘이다: 문서 평가의 종목
-- 후보 목록(`modules/assessment.py`)과 네이버 기업 리포트 필터(`modules/collectors/document/
-- documents.py`). 시세·수급·봉이 있어야 성립하는 조회는 `select_watched.sql`을 읽어야 한다.
-- `is_watched`는 "시세까지 받는 종목"이고, 그것이 켜는 것에는 추론 subject와 기술지표
-- 조회가 들어 있다. 셋째 소비자를 붙이기 전에 그 술어에 맞는지 먼저 본다.
--
-- 자유 문자열 태그를 받으면 document_instrument가 instrument와 조인되지 않는다. 그래서
-- 허용 값을 마스터가 정한다.
--
-- **한 티커가 두 시장에 있으면 줄이 둘이 된다.** 유니크 키가 `(ticker, market)`이기
-- 때문이다. 지금은 전부 kospi라 생기지 않고, 생기더라도 프롬프트에 같은 줄이 두 번
-- 실릴 뿐이다(태그 검증은 티커 집합으로 한다).
SELECT ticker, name
FROM instrument
ORDER BY ticker
