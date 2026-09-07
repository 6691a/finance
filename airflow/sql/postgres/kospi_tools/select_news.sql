-- `recent_news` 툴이 읽는 평가된 문서. 기존 추론의 같은 쿼리를 베끼지 않고 새로 만든다 —
-- 한쪽을 고칠 때 다른 쪽이 조용히 따라 바뀌는 것을 막는다.
--
-- **창의 끝은 벽시계가 아니라 슬롯이 정한 `as_of_at`이다.** 술어를 event-time 컬럼 셋에
-- 모두 건다. `detected_at`은 언제 봤나, `assessed_at`은 언제 평가했나, `updated_at`은 본문이
-- 언제 갱신됐나다. 셋 다 걸어야 "그 시각에 알 수 있었던 것"에 가까워진다.
--
-- 제목·점수만 주면 모델이 근거를 지어낸다. 평가가 남긴 `reason`과 `new_facts`를 함께 준다 —
-- 둘 다 컬럼이 아니라 `assessment` JSONB 안의 키다.
--
-- 종목 태그를 배열로 접어 문서당 한 행을 지킨다. 대표에 연결된 중복은 뺀다.
--
-- 점수 하한(`min_value_score`)은 **모델이 고르는 인자가 아니라 코드 상수**다
-- (`domain.NEWS_MIN_VALUE_SCORE`, 2026-09-04). `LIMIT`이 하한을 대신한다는 2026-09-03의
-- 판단은 창이 넓을 때만 참이었다 — 좁은 창은 후보가 30건 밑이라 `LIMIT`이 아무것도 자르지
-- 않고 창 안 전부가 그대로 간다. 근거는 상수 옆에 있다.
--
-- **`total`도 하한 뒤로 센다.** 잘림 표시가 "네가 볼 수 있었던 것 중 몇 건"이어야 하고,
-- 하한이 버린 것까지 세면 모델이 못 본 쓰레기를 사건으로 남겨 둔다.
WITH bounds AS (
    SELECT %(window_start)s::timestamptz AS window_start,
           %(as_of_at)s::timestamptz AS as_of_at
)
SELECT document.id,
       document.title,
       document.source_slug,
       document.published_at,
       document.value_score,
       document.direction,
       document.assessment ->> 'reason' AS reason,
       document.assessment -> 'new_facts' AS new_facts,
       coalesce(
           array_agg(tag.ticker ORDER BY tag.ticker) FILTER (WHERE tag.ticker IS NOT NULL),
           '{}'
       ) AS tickers,
       -- 창 안의 후보 전체 수. `LIMIT` 앞에서 세므로 돌려준 행 수와 다르면 잘린 것이다.
       count(*) OVER () AS total
FROM document
CROSS JOIN bounds
LEFT JOIN document_instrument AS tag
       ON tag.document_id = document.id
WHERE document.canonical_document_id IS NULL
  AND document.assessed_at IS NOT NULL
  AND document.detected_at >= bounds.window_start
  AND document.detected_at <= bounds.as_of_at
  AND document.assessed_at <= bounds.as_of_at
  AND document.updated_at <= bounds.as_of_at
  -- 하한 밑은 전망의 근거가 될 수 없는 문서다(부고·인사·행사).
  AND document.value_score >= %(min_value_score)s
GROUP BY document.id
ORDER BY document.value_score DESC NULLS LAST, document.assessed_at DESC
LIMIT %(limit)s
