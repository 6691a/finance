-- 규제 공시·실적 수집 대상. 번호가 있다는 것이 곧 대상이라는 뜻이다.
-- `market`을 함께 거는 이유는 발급 기관이 시장마다 다르기 때문이다 — 한국은 DART 회사
-- 고유번호이고 미국은 SEC EDGAR CIK다. 이 조건이 없으면 나중에 미국 종목의 CIK가 채워질 때
-- DART 수집기가 그 번호를 자기 것으로 알고 조회해 조용한 0건을 받는다.
SELECT ticker, name, filing_entity_id, sector
FROM instrument
WHERE filing_entity_id IS NOT NULL
  AND market IN ('kospi', 'kosdaq')
ORDER BY ticker
