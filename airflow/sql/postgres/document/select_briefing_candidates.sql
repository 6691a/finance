-- 창 안에서 평가한 문서 중 점수가 높은 쪽 몇십 건. **무엇을 보여 줄지 여기서 정하지 않는다.**
-- 최종 선별은 `modules/briefing/picks.py`가 목록 전체를 한 번에 보고 한다. 여기까지가
-- value_score의 몫이다. 점수는 잘 갈라지지만 **상위 구간은 거의 동점이라**(실측에서 후보
-- 60건의 최저가 5점, 그 안에 5점만 28건) 동점 사이 순서는 assessed_at이 정한 최신순일 뿐이다.
-- LIMIT이 그 체이고 동시에 모델에 실리는 토큰의 상한이다.
-- 티커는 배열로 접어 문서당 한 행을 지킨다. 조인으로 펼치면 태그 수만큼 같은 기사가 나온다.
SELECT document.id,
       document.title,
       document.source_slug,
       document.direction,
       document.value_score,
       document.canonical_url,
       document.assessment ->> 'reason' AS reason,
       coalesce(
           array_agg(tag.ticker ORDER BY tag.ticker) FILTER (WHERE tag.ticker IS NOT NULL),
           '{}'
       ) AS tickers
FROM document
LEFT JOIN document_instrument AS tag
       ON tag.document_id = document.id
WHERE document.assessed_at >= %s
  AND document.canonical_document_id IS NULL
GROUP BY document.id
ORDER BY document.value_score DESC NULLS LAST, document.assessed_at DESC
LIMIT %s
