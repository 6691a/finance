-- 대상에 태그된 그 주의 평가 문서. 대상당 상위 몇 건만.
--
-- 주당 평가 문서가 1,000건을 넘어(2026-08-27 실측: 최근 두 주 1,424건, 1,096건) 전부
-- 프롬프트에 실을 수 없다. 여기서 좁히고 모자라면 모델이 툴로 더 판다.
--
-- **태그 테이블 둘을 합친다.** 종목은 `document_instrument`에 붙지만 `document_indicator`에도
-- `kis 005930`으로 붙어 있고, 지수·환율·금리는 `document_indicator`에만 있다. 한쪽만 보면
-- 같은 대상의 문서를 절반쯤 잃는다.
--
-- **`series_id`만으로 건다.** 관측값 조회라면 `(provider, series_id)`를 함께 걸어야 하지만
-- 이것은 태그 조회이고, 실측에서 같은 `series_id`가 두 제공처에 걸친 경우가 없다
-- (2026-08-27 확인). 대상 목록도 코드가 정한 고정 집합이라 충돌이 생기지 않는다.
--
-- **`as_of_at` cutoff는 여기에 건다.** 근거는 "그 시점에 알 수 있었던 것"이어야 한다 —
-- 실현 등락과 반대다. `detected_at`과 `assessed_at` 둘 다 봐야 뒤늦게 평가된 문서가
-- 안 새어 든다.
WITH tagged AS (
    SELECT document_id, ticker AS target_code
    FROM document_instrument
    WHERE ticker = ANY(%(codes)s)
    UNION
    SELECT document_id, series_id AS target_code
    FROM document_indicator
    WHERE series_id = ANY(%(codes)s)
),
ranked AS (
    SELECT tagged.target_code,
           document.id,
           document.title,
           document.summary,
           document.source_slug,
           document.published_at,
           document.value_score,
           document.direction,
           row_number() OVER (
               PARTITION BY tagged.target_code
               ORDER BY document.value_score DESC, document.published_at DESC, document.id
           ) AS rank_in_target
    FROM tagged
    JOIN document ON document.id = tagged.document_id
    WHERE document.assessed_at IS NOT NULL
      AND document.published_at >= %(week_start_at)s
      AND document.published_at < %(week_after_at)s
      AND document.detected_at < %(as_of_at)s
      AND document.assessed_at < %(as_of_at)s
)
SELECT target_code, id, title, summary, source_slug, published_at, value_score, direction
FROM ranked
WHERE rank_in_target <= %(per_target)s
ORDER BY target_code, rank_in_target
