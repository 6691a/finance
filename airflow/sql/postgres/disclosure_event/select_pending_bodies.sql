-- 본문을 아직 못 받은 **시장 반응형** 공시. 이 조회가 재시도 목록이라 별도 작업 큐가 없다
-- (`select_pending_earnings.sql`과 같은 구조다).
--
-- **종류를 좁히는 것이 이 쿼리의 핵심이다.** 원문 크기가 종류마다 자릿수로 갈린다
-- (2026-08-29 실측: 조회공시요구 220자, 파생상품거래손실발생 921자, 동일인등 거래변경
-- 1,943자, **반기보고서 638,116자**). 정기보고서를 받으면 한 건이 프롬프트 예산을 통째로
-- 먹고, 그 내용은 인과 사건도 아니다.
--
-- 좁히는 목록은 SQL이 아니라 파이썬이 준다(`MATERIAL_REPORT_KEYWORDS`). 한 종류를 더할 때
-- 고칠 자리를 하나로 두려는 것이고, 그 상수는 `dart.py`가 본문을 받을지 정하는 값과 같다.
--
-- **`receipt_date` 하한을 건다.** 안 걸면 첫 실행이 4,000건을 통째로 집는다. 과거를 채우려면
-- DAG의 `lookback_days`를 늘려 여러 번 돌린다 — 한 번에 다 받는 것보다 안전하다.
SELECT
    corp_code,
    company_name,
    stock_code,
    corp_class,
    report_name,
    rcept_no,
    filer_name,
    receipt_date,
    remarks
FROM disclosure_event
WHERE provider = 'dart'
  AND stock_code = ANY(%(stock_codes)s)
  AND receipt_date >= %(since)s
  AND body IS NULL
  AND report_name LIKE ANY(%(patterns)s)
ORDER BY receipt_date DESC, rcept_no
LIMIT %(limit)s
