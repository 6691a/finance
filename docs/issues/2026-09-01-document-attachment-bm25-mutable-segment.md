# `document_attachment` BM25 mutable 세그먼트 검색 지연

- 상태: 원인 확인, 복구 미적용 (2026-09-01)
- 대상: ParadeDB `pg_search` 0.25.2, `document_attachment_bm25`
- 결론: `mutable_segment_rows=50`으로 낮춘 뒤 `REINDEX INDEX CONCURRENTLY`로 기존 적체를 다시 만든다.

## 증상

PDF 파싱 결과는 `document_attachment.extracted_text`에 정상 저장됐지만, 해당 컬럼의 BM25 검색은 끝나지 않았다. 같은 데이터의 단순 `LIKE` 검색은 약 80ms에 끝났으므로 원문 저장이나 PostgreSQL 자체 읽기 문제는 아니었다.

확인 당시 주요 상태는 다음과 같았다.

| 항목 | `document` | `document_attachment` |
| --- | ---: | ---: |
| 행 수 | 4,073 | 1,350 |
| 갱신 횟수 | 11,070 | 775 |
| HOT 갱신 | 5,026 | 1 |
| BM25 immutable 세그먼트 | 3,864건, 삭제 0건 | live 637건, 삭제 표시 630건 |
| BM25 mutable 세그먼트 | 498건 | 857건 |
| 검색 | 약 0.7초 | 제한 시간 안에 끝나지 않음 |

첨부 테이블에는 파싱 완료 775건, 비어 있지 않은 `extracted_text` 774건, 총 약 1,597만 자가 있었다. `document_attachment_bm25`는 valid, ready 상태였으므로 색인이 없거나 깨진 상태는 아니었다.

> 세그먼트의 `num_docs=637`, `num_deleted=630`, `max_doc=1267`에서 삭제 비율은 `630 / 1267`, 약 49.7%다. `num_docs`는 삭제 문서를 제외한 live 문서 수이므로 `630 / 637`을 삭제 비율로 보면 안 된다.

## 원인

첨부 하나는 두 단계로 저장된다.

1. [`upsert.sql`](../../airflow/sql/postgres/document_attachment/upsert.sql)이 텍스트 없는 첨부 행을 INSERT한다.
2. PDF 파서가 파일을 읽은 뒤 [`update_parse.sql`](../../airflow/sql/postgres/document_attachment/update_parse.sql)로 `extracted_text`를 UPDATE한다.

`extracted_text`는 [`document_attachment_bm25`](../../migrations/versions/f1a47d0c62b8_add_document_bm25_indexes.py)의 색인 표현식에 직접 들어간다. 따라서 이 UPDATE는 대부분 HOT 갱신이 될 수 없고, 기존 색인 문서를 삭제 표시한 뒤 새 문서를 추가한다. 삭제 표시 630건은 같은 행을 여러 번 갱신해서 생긴 것이 아니라 대체로 첨부마다 한 번씩 생긴 이전 색인 문서다.

ParadeDB 0.25.2의 `mutable_segment_rows` 기본값은 1,000이다. 확인 당시 새 색인 문서 857건은 임계값에 못 미쳐 mutable 세그먼트에 남아 있었다.

mutable은 “아직 BM25 색인이 전혀 없다”거나 “원문이 메모리에만 있다”는 뜻이 아니다. 원문은 PostgreSQL 테이블에 영구 저장되고, mutable 세그먼트에는 변경된 행을 찾아갈 정보가 저장된다. 검색할 때 ParadeDB가 이 행들을 읽어 varlena 값을 풀고, 한국어 토크나이징과 Tantivy RAM 세그먼트 생성을 동기적으로 수행한다. 이 환경에서는 약 1,597만 자를 검색 요청 안에서 처리하면서 병목이 발생했다.

```text
PDF 파싱 UPDATE
      │
      ├─ 이전 색인 문서 삭제 표시
      └─ 새 문서가 mutable 세그먼트에 누적
                           │
                           └─ BM25 SELECT 때 RAM 색인 생성 비용 발생
```

이는 “한 시간이 지나면 자동으로 검색 가능해지는” 구조가 아니다. 행 수나 세그먼트 병합 조건이 충족되고 후속 쓰기·병합 계기가 생겨야 immutable 세그먼트가 된다. 첨부는 보통 한 번 파싱한 뒤 다시 쓰지 않으므로 정리 계기도 적었다.

autovacuum은 2026-09-01 01:10에 실행됐다. 이 과정에서 오래된 색인 문서가 삭제 표시됐을 가능성이 크지만, non-frozen mutable 세그먼트까지 영구 세그먼트로 병합하지는 못했다. 따라서 VACUUM만 반복하는 것은 이 적체의 해결책이 아니다.

## 해결 방법

초기 운영값은 `mutable_segment_rows=50`으로 한다. 시간당 파싱 기본 배치가 50건이므로 active mutable 세그먼트를 기본값 1,000보다 훨씬 작게 유지할 수 있다.

단, 설정만 바꿔도 이미 쌓인 857건이 소급해서 immutable 세그먼트로 바뀌지는 않는다. 기존 적체를 없애려면 설정 변경 뒤 색인을 다시 만들어야 한다.

```sql
ALTER INDEX document_attachment_bm25
SET (mutable_segment_rows = 50);

REINDEX INDEX CONCURRENTLY document_attachment_bm25;
```

`REINDEX INDEX CONCURRENTLY`는 현재 live 테이블 행으로 새 BM25 색인을 만든 뒤 교체하므로 검색 중단을 줄인다. PostgreSQL 트랜잭션 블록 안에서는 실행할 수 없다. 저장소에 반영할 때는 이미 적용된 기존 리비전을 수정하지 말고 새 Alembic 리비전을 만들며, REINDEX는 `autocommit_block()` 안에서 실행한다.

`50`은 active mutable 세그먼트의 동결 임계값이지, 모든 순간의 RAM 처리 대상을 정확히 50건 이하로 보장하는 상한은 아니다. 동결된 세그먼트도 병합 조건을 기다릴 수 있으므로 적용 후 실제 세그먼트와 검색 시간을 다시 확인한다.

## 적용 순서

1. 가능하면 파싱 대기 건수가 0이 된 뒤 작업한다. 대기 중에도 적용할 수 있지만, 이후 UPDATE가 다시 새 mutable 세그먼트를 만든다.
2. `mutable_segment_rows=50`을 설정한다.
3. `REINDEX INDEX CONCURRENTLY document_attachment_bm25`를 실행한다.
4. 색인 상태와 실제 BM25 검색을 검증한다.

운영 반영 전후 확인 예시는 다음과 같다.

```sql
SELECT c.reloptions, i.indisvalid, i.indisready, i.indislive
FROM pg_class AS c
JOIN pg_index AS i ON i.indexrelid = c.oid
WHERE c.oid = 'document_attachment_bm25'::regclass;

SELECT *
FROM pdb.index_segments('document_attachment_bm25');

SET statement_timeout = '30s';
SELECT id
FROM document_attachment
WHERE id @@@ 'extracted_text:도매'
ORDER BY pdb.score(id) DESC
LIMIT 10;
RESET statement_timeout;
```

성공 기준은 다음과 같다.

- `reloptions`에 `mutable_segment_rows=50`이 보인다.
- 색인이 valid, ready, live 상태다.
- 기존 857건 규모의 mutable 적체가 사라졌다.
- 첨부 BM25 검색이 제한 시간 안에 끝난다.
- REINDEX가 만든 임시 또는 invalid 색인이 남지 않는다.

## 대안과 롤백

50에서도 검색 지연이 반복되고 파싱 직후 검색 가능성이 더 중요하다면 `mutable_segment_rows=0`을 검토한다. 이 값은 mutable 세그먼트를 비활성화해 색인 비용을 쓰기 쪽으로 옮기지만, 작은 세그먼트와 쓰기 비용이 늘 수 있어 첫 선택으로 사용하지 않는다.

설정만 되돌리려면 다음과 같이 기본값을 복원한다.

```sql
ALTER INDEX document_attachment_bm25
RESET (mutable_segment_rows);
```

설정을 되돌린 뒤 물리적 색인 구성까지 다시 맞출 필요가 있을 때만 concurrent reindex를 한 번 더 수행한다.

## 참고

- ParadeDB 0.25.2 소스: [mutable 임계값과 freeze](https://github.com/paradedb/paradedb/blob/v0.25.2/pg_search/src/postgres/storage/block.rs#L441-L460)
- ParadeDB 0.25.2 소스: [검색 시 mutable 세그먼트를 RAM에 여는 경로](https://github.com/paradedb/paradedb/blob/v0.25.2/pg_search/src/index/directory/mvcc.rs#L203-L230)
- ParadeDB 0.25.2 소스: [행을 읽어 메모리 색인을 만드는 경로](https://github.com/paradedb/paradedb/blob/v0.25.2/pg_search/src/index/directory/mvcc.rs#L634-L960)
