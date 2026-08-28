-- 그 주 평가된 문서 중 `value_score` 상위 몇 건. 프롬프트에 실을 근거 후보다.
--
-- 주당 평가 문서가 1,000건을 넘어(2026-08-27 실측: 최근 두 주 1,424건, 1,096건) 전부
-- 실을 수 없다. 여기서 좁힌다.
--
-- **대상 코드로 좁히지 않는다**(2026-08-28 정정). 처음에는 대상에 태그된 것만 뽑았는데,
-- 그러면 대상 목록 밖 지표에만 태그된 문서가 통째로 빠진다. 실측에서 `CPI_M`에만 붙은
-- 미국 물가 기사 8건이 그렇게 사라졌고, **정작 모델은 그 사건을 경로 열넷 중 여덟의
-- 출발점으로 썼다** — 근거는 다른 태그가 우연히 겹친 것뿐이었다.
--
-- 대상 목록은 코드 상수라 `WTI`·`DXY`·`USDJPY`·`JGB10Y`처럼 시장을 움직이는 값이 거기
-- 없을 수 있다. 근거는 그것들까지 봐야 하고, **어느 대상에 닿았는지는 모델이 판단한다.**
--
-- 태그는 표시용으로 함께 낸다. 어느 종목·지표에 붙은 기사인지를 알아야 모델이 사건을
-- 정확히 만든다. 태그가 하나도 없는 문서(시황·경제 일반)도 후보가 된다.
--
-- **`as_of_at` cutoff는 여기에 건다.** 근거는 "그 시점에 알 수 있었던 것"이어야 한다 —
-- 실현 등락과 반대다. `detected_at`과 `assessed_at` 둘 다 봐야 뒤늦게 평가된 문서가
-- 안 새어 든다.
WITH tagged AS (
    SELECT document_id, ticker AS code FROM document_instrument
    UNION
    SELECT document_id, series_id AS code FROM document_indicator
)
SELECT document.id,
       document.title,
       document.summary,
       document.source_slug,
       document.published_at,
       document.value_score,
       document.direction,
       coalesce(
           array_agg(DISTINCT tagged.code) FILTER (WHERE tagged.code IS NOT NULL),
           ARRAY[]::text[]
       ) AS tags
FROM document
LEFT JOIN tagged ON tagged.document_id = document.id
WHERE document.assessed_at IS NOT NULL
  AND document.published_at >= %(week_start_at)s
  AND document.published_at < %(week_after_at)s
  AND document.detected_at < %(as_of_at)s
  AND document.assessed_at < %(as_of_at)s
GROUP BY document.id,
         document.title,
         document.summary,
         document.source_slug,
         document.published_at,
         document.value_score,
         document.direction
ORDER BY document.value_score DESC, document.published_at DESC, document.id
LIMIT %(limit)s
