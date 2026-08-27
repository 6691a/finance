-- `slack_disclosure_briefing`이 읽는, 그 창의 공시에 붙어 있는 실적 숫자.
--
-- **연결(CFS)을 우선한다.** 같은 접수번호에 연결과 별도가 함께 오면 연결만 그린다. 둘을
-- 한 줄에 섞으면 읽는 사람이 어느 범위인지 못 가른다. 연결이 없을 때만 별도가 남고,
-- 어느 쪽인지는 `statement_scope`를 그대로 실어 화면이 밝힌다.
--
-- **기간 기준 둘(`period`·`cumulative`)을 다 준다.** 처음에는 `period`만 줬는데 그러면
-- 정기보고서의 전년 대비가 영영 안 나온다 — OpenDART가 `frmtrm_amount`(전년 3개월)를 주지
-- 않고 `frmtrm_add_amount`(전년 누계)만 준다(2026-08-27 실측). 반대로 잠정실적 공시는 원문
-- 표에 두 기준의 전년값이 다 있다. 어느 쪽이 비교 가능한지가 공시 종류마다 다르므로
-- **여기서 고르지 않고 둘 다 주고 화면이 기준별로 줄을 나눈다.**
--
-- 전년 대비는 여기서 계산하지 않는다. `prior_year_amount`를 그대로 주고
-- `modules/briefing/disclosures.py`의 순수 함수가 나눈다 — 0 나누기와 결측 처리를
-- SQL에 흩지 않는다.
--
-- 정렬은 기준(3개월 먼저) · 지표(매출 → 영업이익 → 당기순이익) 고정이다. 화면마다 순서가
-- 달라지면 눈으로 비교되지 않는다.
SELECT earnings_fact.rcept_no,
       earnings_fact.metric,
       earnings_fact.statement_scope,
       earnings_fact.amount_basis,
       earnings_fact.period_end,
       earnings_fact.current_amount,
       earnings_fact.prior_year_amount
FROM earnings_fact
WHERE earnings_fact.provider = 'dart'
  AND earnings_fact.rcept_no = ANY(%s)
  AND earnings_fact.statement_scope = (
      SELECT scoped.statement_scope
      FROM earnings_fact AS scoped
      WHERE scoped.provider = earnings_fact.provider
        AND scoped.rcept_no = earnings_fact.rcept_no
      ORDER BY CASE scoped.statement_scope WHEN 'CFS' THEN 0 ELSE 1 END
      LIMIT 1
  )
ORDER BY earnings_fact.rcept_no,
         CASE earnings_fact.amount_basis WHEN 'period' THEN 0 ELSE 1 END,
         CASE earnings_fact.metric
             WHEN 'revenue' THEN 0
             WHEN 'operating_profit' THEN 1
             ELSE 2
         END
