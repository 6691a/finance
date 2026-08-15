-- 수집을 켜 둔 출처. 목록의 원본은 코드가 아니라 이 테이블이다.
-- 이용조건은 출처마다 다르고 바뀌므로 한 곳을 멈추는 일이 배포가 되면 안 된다.
SELECT
    slug,
    name,
    source_kind,
    country,
    language,
    feed_url,
    collection_mode
FROM document_source
WHERE enabled
ORDER BY slug
