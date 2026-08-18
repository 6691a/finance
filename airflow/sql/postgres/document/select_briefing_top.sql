-- 창 안에서 평가한 문서 중 점수가 높은 몇 건.
-- **이 쿼리가 value_score의 소비자다.** 저장 단계는 점수로 문서를 버리지 않고, 무엇을 보여
-- 줄지는 여기서만 정한다. 기준을 바꾸고 싶으면 ORDER BY와 LIMIT만 고치면 된다.
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
GROUP BY document.id
ORDER BY document.value_score DESC NULLS LAST, document.assessed_at DESC
LIMIT %s
