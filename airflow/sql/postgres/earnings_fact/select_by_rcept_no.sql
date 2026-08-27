-- `slack_disclosure_briefing`이 읽는, 그 창의 공시에 붙어 있는 실적 숫자.
--
-- **연결(CFS)을 우선한다.** 같은 접수번호에 연결과 별도가 함께 오면 연결만 그린다. 둘을
-- 한 줄에 섞으면 읽는 사람이 어느 범위인지 못 가른다. 연결이 없을 때만 별도가 남고,
-- 어느 쪽인지는 `statement_scope`를 그대로 실어 화면이 밝힌다.
--
-- **`amount_basis = 'period'`만 본다.** 누계(`cumulative`)는 같은 지표의 다른 값이라
-- 함께 실으면 자릿수가 두 배로 보인다. 분기 실적이 이 알림의 관심사다.
--
-- 전년 대비는 여기서 계산하지 않는다. `prior_year_amount`를 그대로 주고
-- `modules/briefing/disclosures.py`의 순수 함수가 나눈다 — 0 나누기와 결측 처리를
-- SQL에 흩지 않는다.
--
-- 지표 순서는 매출 → 영업이익 → 당기순이익으로 고정한다. 화면마다 순서가 달라지면
-- 눈으로 비교되지 않는다.
SELECT earnings_fact.rcept_no,
       earnings_fact.metric,
       earnings_fact.statement_scope,
       earnings_fact.period_end,
       earnings_fact.current_amount,
       earnings_fact.prior_year_amount
FROM earnings_fact
WHERE earnings_fact.provider = 'dart'
  AND earnings_fact.rcept_no = ANY(%s)
  AND earnings_fact.amount_basis = 'period'
  AND earnings_fact.statement_scope = (
      SELECT scoped.statement_scope
      FROM earnings_fact AS scoped
      WHERE scoped.provider = earnings_fact.provider
        AND scoped.rcept_no = earnings_fact.rcept_no
        AND scoped.amount_basis = 'period'
      ORDER BY CASE scoped.statement_scope WHEN 'CFS' THEN 0 ELSE 1 END
      LIMIT 1
  )
ORDER BY earnings_fact.rcept_no,
         CASE earnings_fact.metric
             WHEN 'revenue' THEN 0
             WHEN 'operating_profit' THEN 1
             ELSE 2
         END
