# `document_attachment` BM25 mutable 세그먼트 검색 지연

- 상태: **해결. 리비전 `70e8e9ce64d3` 반영 대기** (원인 확인 2026-09-01, 처방 확정 2026-09-02)
- 대상: ParadeDB `pg_search` 0.25.2, `document_bm25`·`document_attachment_bm25`
- 결론: 두 인덱스 모두 `mutable_segment_rows=0`으로 끄고, 이미 쌓인 적체는 `REINDEX`가 아니라
  **`VACUUM`**이 띄우는 백그라운드 머지로 없앤다.

> **2026-09-02에 처방이 바뀌었다.** 초판은 `mutable_segment_rows=50` + `REINDEX INDEX
> CONCURRENTLY`였다. 왜 바뀌었는지는 아래 "2026-09-02 재조사"에 있다. 초판이 지목한 원인
> 자체는 맞았고 바뀐 것은 값과 복구 수단이다.

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

autovacuum은 2026-09-01 01:10에 실행됐다. 이 과정에서 오래된 색인 문서가 삭제 표시됐을 가능성이 크지만, non-frozen mutable 세그먼트까지 영구 세그먼트로 병합하지는 못했다.

## 2026-09-02 재조사 — 값과 복구 수단을 바꿨다

누군가 운영 `document_attachment_bm25`에 `mutable_segment_rows=5`를 손으로 넣어 둔 상태였다
(저장소에는 그 값이 없었다). 그 상태에서 다시 재고, `pg_search` v0.25.2 소스를 읽어 초판의
처방 둘을 고쳤다.

### 다시 잰 값 (읽기 전용 조회)

| 항목 | 값 |
| --- | --- |
| 첨부 텍스트 | ok 975건, 평균 20,531자, p95 84,892자, 최대 307,511자 |
| 첨부 인덱스 | 세그먼트 13개, mutable 1개(3행). 검색 3ms |
| 문서 인덱스 | 세그먼트 3개, **mutable 1개(80행)**. 검색 **145~160ms** |
| `document.body`가 PDF 바이트인 행 | 0건 (`fc04660`으로 이미 해결) |

**지연은 첨부만의 문제가 아니었다.** `document_bm25`는 손댄 적이 없어 기본값 1,000이고,
버퍼에 80행(평균 1.8천 자)만 있는데도 매 질의 145ms다. 1,000행이 차면 약 2초다. 첨부가 3ms인
것은 손으로 넣은 5 때문이지 첨부가 원래 괜찮아서가 아니다.

### 값을 50이 아니라 0으로 한 이유

`mutable_segment_rows`의 허용 범위는 0~10,000이고 **0이면 버퍼 자체를 안 쓴다**
(`postgres/insert.rs`의 `InsertMode::Immutable`). 형태소 분석 비용은 쓰는 쪽, 즉 시간당 DAG이
그 자리에서 한 번 문다.

- **작은 값은 문제를 줄일 뿐 성격을 안 바꾼다.** 5든 50이든 그 안의 행은 여전히 질의마다 다시
  분석된다. 첨부 텍스트는 한 건이 평균 2만 자, 최대 30만 자라 **행 수가 비용의 척도가 아니다.**
  50행이 최악의 경우 1,500만 자다.
- **작은 값은 세그먼트를 잘게 만든다.** 5행마다 세그먼트가 생기고, mutable 세그먼트가 셋을
  넘으면 쓰는 쪽이 foreground 머지를 문다(`postgres/merge.rs`의 `need_backpressure`).
- **이 저장소의 쓰기 패턴에 버퍼가 필요 없다.** 버퍼는 초당 수천 건을 쓰는 워크로드용이다.
  여기는 시간당 수십~수백 행을 배치로 쓰고 하루 수십 회 읽는다. 얻는 것이 없고 읽기만 비싸다.

초판이 0을 "첫 선택으로 쓰지 않는다"고 한 근거는 "작은 세그먼트와 쓰기 비용이 늘 수 있다"였다.
쓰기 비용은 사실이지만 시간당 배치가 무는 값이고, 작은 세그먼트는 오히려 **작은 값 쪽에서**
생긴다.

### `REINDEX`가 아니라 `VACUUM`인 이유

옵션을 0으로 바꿔도 **이미 쌓인 mutable 세그먼트는 저절로 사라지지 않는다**(초판의 관찰이
맞다). 다만 그것을 없애는 데 REINDEX까지 갈 필요가 없다.

- 0이 되면 기존 mutable 세그먼트는 mergeable로 판정된다(`storage/block.rs`의 `is_mergeable`).
- **INSERT 시점의 머지는 그것을 집지 않았고, `VACUUM`이 띄운 백그라운드 머지가 immutable로
  바꿨다** — 로컬 ParadeDB 0.25.2에서 80행이 3초 안에 바뀌는 것을 확인했다.
- `paradedb.force_merge`는 0.25.2에서 deprecated이고 호출하면
  `force_merge is deprecated, run VACUUM instead`를 낸다(`api/admin.rs`).
- REINDEX는 전체 재색인이라 훨씬 비싸다. 필요한 것은 버퍼에 남은 수십~수백 행의 변환뿐이다.

## 적용

리비전 [`70e8e9ce64d3`](../../migrations/versions/70e8e9ce64d3_disable_bm25_mutable_segments.py)이
다음을 한다. `ALTER` 둘은 트랜잭션 안에서, `VACUUM`은 `autocommit_block()` 안에서 돈다.

```sql
ALTER INDEX document_bm25 SET (mutable_segment_rows = 0);
ALTER INDEX document_attachment_bm25 SET (mutable_segment_rows = 0);
VACUUM document;
VACUUM document_attachment;
```

`ALTER`는 인덱스에 AccessExclusiveLock을 잡지만 O(1)이라 시간당 DAG을 막지 않는다. 파싱 대기
건수가 0이 될 때까지 기다릴 필요도 없다 — 이후 UPDATE는 버퍼 없이 바로 색인된다.

`tests/migrations/test_document_schema.py`의
`test_bm25_indexes_write_without_a_mutable_segment`가 오프라인 SQL에서 네 문장을 대조한다.

## 검증

```sql
SELECT c.relname, c.reloptions, i.indisvalid, i.indisready, i.indislive
FROM pg_class AS c
JOIN pg_index AS i ON i.indexrelid = c.oid
WHERE c.relname IN ('document_bm25', 'document_attachment_bm25');

SELECT mutable, count(*), sum(num_docs)
FROM paradedb.index_info('document_bm25')
GROUP BY 1;

SET statement_timeout = '30s';
SELECT id, paradedb.score(id) AS s
FROM document WHERE body @@@ '기준금리' ORDER BY s DESC LIMIT 10;
RESET statement_timeout;
```

> 조회 함수는 `paradedb.index_info(...)`다. 초판에 적힌 `pdb.index_segments(...)`는 0.25.2에
> 없다.

성공 기준:

- 두 인덱스의 `reloptions`에 `mutable_segment_rows=0`이 보인다.
- `index_info`에 `mutable = true`인 세그먼트가 없다. 새 문서를 파싱한 뒤에도 안 생긴다.
- `document` BM25 검색이 145ms에서 수 ms로 줄고, 첨부 검색은 3ms를 유지한다.
- 색인은 valid, ready, live 상태 그대로다(REINDEX가 없으므로 교체 자체가 없다).

로컬 ParadeDB(`compose/local`)에서 리비전을 실제로 적용해 위 셋을 확인했다(2026-09-02).
`document_bm25`가 세그먼트 1개(2,947행), mutable 없음이었다.

## 롤백

```sql
ALTER INDEX document_bm25 RESET (mutable_segment_rows);
ALTER INDEX document_attachment_bm25 RESET (mutable_segment_rows);
```

리비전의 `downgrade`가 이 둘이다. 되돌리면 기본값 1,000으로 돌아가고 지연도 함께 돌아온다.

## 참고

- ParadeDB 0.25.2 소스: [mutable 임계값과 freeze](https://github.com/paradedb/paradedb/blob/v0.25.2/pg_search/src/postgres/storage/block.rs#L441-L460)
- ParadeDB 0.25.2 소스: [검색 시 mutable 세그먼트를 RAM에 여는 경로](https://github.com/paradedb/paradedb/blob/v0.25.2/pg_search/src/index/directory/mvcc.rs#L203-L230)
- ParadeDB 0.25.2 소스: [행을 읽어 메모리 색인을 만드는 경로](https://github.com/paradedb/paradedb/blob/v0.25.2/pg_search/src/index/directory/mvcc.rs#L634-L960)
- ParadeDB 0.25.2 소스: [버퍼를 쓸지 정하는 자리](https://github.com/paradedb/paradedb/blob/v0.25.2/pg_search/src/postgres/insert.rs#L105-L124)
- ParadeDB 0.25.2 소스: [`force_merge`는 deprecated](https://github.com/paradedb/paradedb/blob/v0.25.2/pg_search/src/api/admin.rs#L882-L898)
- ParadeDB 문서: [Write Throughput](https://www.paradedb.com/docs/documentation/performance-tuning/writes) — "A higher value generally improves write throughput at the expense of read performance, since the mutable data structure is slower to search."
- 설계 문서: [pdf-parsing-bm25.md](../analysis/pdf-parsing-bm25.md) 8.2와 15절 ⑪
