-- 추론 툴 `analyst_opinions`가 쓴다. 종목 하나의 최근 투자의견을 최신 발표일부터 준다.
--
-- **당일 행을 빼지 않는다.** `short_and_credit`이 당일을 빼는 것은 KIS가 장중에 당일
-- 공매도를 0으로 보내기 때문이고, 투자의견은 아침에 발표되는 당일 사건이 정상값이다.
--
-- 창의 끝은 `created_at`으로 자른다. 이 행이 처음 들어온 시각이 "그때 알 수 있었던 것"이다.
-- 08:20 KST 수집이 재시도 끝에 08:35 장전 추론을 넘기면 그날 장전에는 전날치까지만
-- 보인다 — 의도된 동작이다.
--
-- ## 사유를 함께 준다
--
-- KIS는 숫자만 주고 왜 그 의견인지는 안 준다. 그 사유는 같은 증권사가 같은 날 낸 리포트에
-- 있고 그것은 `document`에 있다(`naver_research_company`). 둘을 LATERAL로 잇는다.
--
-- 잇는 조건 셋:
--
-- 1. 발표일이 같다. 리포트의 `published_at`은 네이버 작성일의 KST 자정이라 KST 날짜로 비교한다.
-- 2. 종목이 같다. 네이버 종목분석 제목은 `종목명: 제목 - 증권사` 꼴이라 `instrument.name`으로
--    맞춘다. `document_instrument` 태그를 쓰지 않는 것은 그 태그가 LLM 평가가 채우는 값이라
--    평가 전에는 비어 있기 때문이다.
-- 3. 증권사가 같다. **KIS 약칭이 네이버 표기의 접두다**(키움 ⊂ 키움증권, 한국투자 ⊂
--    한국투자증권). 제목 끝의 ` - 증권사`를 찾는다.
--
-- 못 찾으면 NULL이다. 네이버에 안 올라온 증권사 리포트가 있고, 유료 전용이라 영영 안 오는
-- 것도 있다. 숫자만이라도 주는 편이 낫다.
--
-- 리포트 본문은 여기서 인용하지 않는다 — 이 툴은 문맥이고, 인용할 `ref`가 붙은 같은 리포트가
-- `recent_documents`로 따로 온다. 그래서 URL도 주지 않는다.
--
-- 주석에 퍼센트 기호를 쓰지 않는다. psycopg가 주석까지 훑어 플레이스홀더로 센다.
SELECT opinion.business_date,
       opinion.broker_name,
       opinion.opinion,
       opinion.previous_opinion,
       opinion.target_price,
       opinion.previous_close,
       opinion.gap_rate,
       report.summary AS report_summary
FROM stock_analyst_opinion AS opinion
LEFT JOIN instrument
       ON instrument.ticker = opinion.stock_code
LEFT JOIN LATERAL (
    SELECT document.summary
    FROM document
    WHERE document.source_slug = 'naver_research_company'
      AND document.canonical_document_id IS NULL
      AND document.created_at <= %(as_of_at)s
      AND document.summary IS NOT NULL
      AND instrument.name IS NOT NULL
      AND strpos(document.title, instrument.name || ': ') = 1
      AND strpos(document.title, ' - ' || opinion.broker_name) > 0
      AND (document.published_at AT TIME ZONE 'Asia/Seoul')::date = opinion.business_date
    ORDER BY document.id
    LIMIT 1
) AS report ON TRUE
WHERE opinion.stock_code = %(stock_code)s
  AND opinion.created_at <= %(as_of_at)s
ORDER BY opinion.business_date DESC, opinion.broker_name
LIMIT %(limit)s
