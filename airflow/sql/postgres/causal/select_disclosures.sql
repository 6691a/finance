-- 그 주 대상 종목의 DART 공시 중 **본문이 있는 것.**
--
-- 점수로는 안 좁힌다. 공시에는 `value_score`에 해당하는 값이 없다. 대신 `body IS NOT NULL`이
-- 종류 필터를 겸한다 — 본문은 수집기의 화이트리스트(`dart.MATERIAL_REPORT_KEYWORDS`)에
-- 걸린 종류에만 채워지므로, 그 목록을 여기 한 벌 더 적지 않아도 같은 판정이 온다.
--
-- **왜 걸러야 했나**(2026-08-28 실측). `임원ㆍ주요주주특정증권등소유상황보고서`가 전체
-- 3,850건 중 3,682건(**95퍼센트**)이었고, 그것을 뺀 뒤에도 남은 것 대부분이 형식 공시였다.
-- 프로토타입 두 번에서 공시 22건 중 인용이 0건이었고, 인용된 두 번은 **환각**이었다 —
-- 모델이 `반기보고서 (2026.06)`을 근거로 달고 "AI 반도체 수출 호황"이라고 썼다.
--
-- **본문이 없으면 아예 안 싣는다.** 보고서명 한 줄로는 모델이 내용을 지어내는 것 말고 할
-- 수 있는 일이 없다. 아직 본문을 못 받은 공시는 다음 폴링이 채우고 그때부터 후보가 된다.
--
-- 대상 종류 중 **종목만** 공시를 갖는다. 지수·환율·금리는 여기 안 걸린다.
--
-- `as_of_at` cutoff를 건다. 근거는 "그 시점에 알 수 있었던 것"이어야 한다.
SELECT stock_code,
       rcept_no,
       company_name,
       report_name,
       receipt_date,
       body
FROM disclosure_event
WHERE stock_code = ANY(%(codes)s)
  AND receipt_date BETWEEN %(week_start)s AND %(week_end)s
  AND detected_at < %(as_of_at)s
  AND body IS NOT NULL
ORDER BY receipt_date, rcept_no
