-- `recent_disclosures` 툴이 읽는 DART 공시. **본문이 있는 것만 준다.**
--
-- 보고서명만으로 인과를 쓰라면 모델은 지어내는 것 말고 할 수 있는 일이 없다. 실제로
-- 그랬다 — 2026-08-29에 `반기보고서`를 근거로 "AI 반도체 수출 호황"이라는 문장이 나왔다.
--
-- `body IS NOT NULL`이 **종류 필터를 겸한다.** 본문은 수집기의 화이트리스트에 걸린 종류에만
-- 채워지므로 종류 목록을 여기 한 벌 더 적지 않는다. 한 종류를 더할 때 고칠 자리가 하나다.
--
-- `receipt_date`가 아니라 `detected_at`으로 거른다. 접수일은 날짜뿐이라 창의 끝을 시각으로
-- 자를 수 없고, 우리가 실제로 알 수 있었던 시점은 감지 시각이다.
--
-- 본문은 앞부분만 자른다. 전문을 실으면 툴 하나가 문자 예산을 다 먹는다.
SELECT rcept_no,
       stock_code,
       company_name,
       report_name,
       receipt_date,
       detected_at,
       left(body, %(body_chars)s) AS body
FROM disclosure_event
WHERE provider = 'dart'
  AND body IS NOT NULL
  AND detected_at >= %(window_start)s
  AND detected_at <= %(as_of_at)s
ORDER BY detected_at DESC
LIMIT %(limit)s
