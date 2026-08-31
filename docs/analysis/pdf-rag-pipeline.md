# PDF 첨부 파싱·외부 분석·RAG 개발 설계

- 기준일: 2026-08-31
- 상태: **미구현. 개발 계약.** 외부 Vision·Embedding 호출은 비활성 상태로 시작한다
- 입력 계약: [문서 본문·첨부 수집](../collection/document-body-collection.md)
- 상위 설계: [경제 문서 아카이브](economic-document-archive-design.md)

수집한 PDF를 Synology NAS에서 PyMuPDF로 파싱하고, 로컬 추출만으로 의미를 보존하기 어려운
표·차트·이미지 영역만 외부 모델 분석 대상으로 준비한다. 외부 결과를 페이지와 좌표에 맞춰
병합한 뒤 Chunking → Embedding → RAG 입력으로 보낸다.

## 0. 범위

### 포함

- PDF 일반 텍스트와 단어 좌표 추출
- 페이지 단위 처리와 선택적 1쪽 PDF 생성
- PDF 내부 이미지와 이미지 좌표 추출
- 페이지 전체 또는 선택 영역 렌더링
- 표·차트·이미지형 페이지의 외부 분석 요청 매니페스트 생성
- 로컬 텍스트와 외부 분석 결과 병합
- 청크 생성과 임베딩 실행 조건
- Synology NAS의 CPU·메모리 제한을 고려한 실행 방식

### 제외

- HWP·HWPX·XLS·XLSX·DOCX 파싱
- NAS에서 실행하는 OCR·Vision 모델
- Docling
- 외부 모델과 Embedding의 실제 제공처·모델 선택
- VectorDB 제품 선택과 검색 API
- 실제 외부 호출 활성화

## 1. 확정 결정

| 항목 | 결정 |
| --- | --- |
| PDF 파서 | PyMuPDF |
| 실행 단위 | 첨부 한 개를 열고 페이지를 순차 처리 |
| 동시 실행 | NAS에서는 파싱 작업 1개 |
| 일반 텍스트 | `page.get_text("text", sort=True)` |
| 텍스트 좌표 | `page.get_text("words", sort=True)` |
| 이미지 메타데이터 | `page.get_image_info(hashes=False, xrefs=False)` |
| 원본 이미지 | 필요할 때만 `document.extract_image(xref)` |
| 표 후보 | `page.find_tables(paths=drawings)`의 bbox |
| 차트 후보 | `page.cluster_drawings(drawings=drawings)`의 bbox |
| 렌더링 | 144 DPI, RGB, `alpha=False` |
| 이미지형 페이지 | 개별 이미지가 아니라 페이지 전체 렌더링 |
| 외부 호출 | 파서와 분리, 기본 `dispatch_allowed=false` |
| 병합 | `source_sha256`·page·bbox를 확인한 결과만 사용 |
| Chunking | 2,000자, overlap 200, 문단 경계 우선 |
| Embedding | 외부 분석 대기 항목이 없을 때만 실행 |

## 2. 전체 흐름

```text
document_attachment(PDF)
        │
        ▼
원본 경로·SHA-256 확인
        │
        ▼
PyMuPDF 페이지 순회
        │
        ├─ 일반 텍스트 + 단어 좌표
        ├─ 이미지 메타데이터 + 좌표
        ├─ 저텍스트 이미지 페이지 → 전체 페이지 렌더링
        ├─ 표 후보 → bbox 렌더링
        └─ 차트 후보 → bbox 렌더링
                     │
                     ▼
          외부 분석 요청 매니페스트
          status=not_called
          dispatch_allowed=false
                     │
           실제 호출 단계는 별도 구현
                     │
                     ▼
페이지별 텍스트 + 외부 분석 결과 병합
                     │
                     ▼
Chunking → Embedding → RAG
```

파싱 단계에는 네트워크 클라이언트를 넣지 않는다. 외부 분석을 활성화하더라도 파서가 아니라
별도 dispatcher가 매니페스트를 읽어 호출한다.

## 3. 입력 계약

처리 대상은 다음 조건을 모두 만족하는 `document_attachment`다.

- `kind='file'`
- `storage_path`가 있고 실제 파일이 존재한다
- 확장자 또는 확인한 미디어 타입이 PDF다
- 저장 파일의 SHA-256이 `document_attachment.sha256`과 같다
- 아직 처리하지 않았거나 원본 SHA가 이전 파싱 시점과 달라졌다

파일이 없거나 SHA가 다르면 파싱하지 않는다. 수집 행과 로컬 파일의 관계가 깨진 상태에서
만든 텍스트는 다른 문서에 붙을 수 있기 때문이다.

## 4. 로컬 파싱

### 4.1 페이지 처리

첨부를 한 번 열고 페이지를 순회한다. 다음 첨부로 넘어가기 전에 문서와 페이지 참조를 닫는다.
전체 코퍼스의 페이지 객체나 메타데이터를 메모리에 누적하지 않는다.

페이지별 산출물은 다음 모양이다.

```json
{
  "page": 1,
  "width": 595.3,
  "height": 841.9,
  "text": "...",
  "words": [
    {"bbox": [72.0, 90.0, 130.0, 104.0], "text": "..."}
  ],
  "images": [
    {"bbox": [50.0, 200.0, 545.0, 600.0], "width": 1500, "height": 900}
  ]
}
```

`words`와 `images`는 병합 위치와 원문 근거를 찾는 좌표다. VectorDB에 좌표 전체를 넣지 않고,
파싱 매니페스트에 보존한 뒤 청크에는 page 범위만 남긴다.

### 4.2 페이지 분리

기본 처리 단위는 원본 PDF 안의 논리 페이지다. 외부 전달이나 장애 재현에 실제 1쪽 PDF가
필요할 때만 새 문서를 열어 다음 방식으로 저장한다.

```python
split.insert_pdf(source, from_page=page_number, to_page=page_number)
```

모든 페이지를 미리 별도 PDF로 만들지 않는다. 같은 내용을 두 벌 저장하고 디스크 I/O만 늘어난다.

### 4.3 이미지 추출과 페이지 렌더링

`extract_image()`는 PDF 내부 이미지 객체 하나가 필요한 경우에만 쓴다. 여러 이미지 조각이
한 페이지 화면을 구성할 수 있으므로, 페이지의 의미 전체가 필요한 이미지형 문서는 개별 객체를
조립하지 않고 페이지 전체를 렌더링한다.

```python
pixmap = page.get_pixmap(dpi=144, clip=bbox, alpha=False)
```

표·차트처럼 좌표가 확정된 대상은 `clip=bbox`, 이미지형 페이지는 `clip=page.rect`를 사용한다.

## 5. 외부 분석 후보 판정

### 5.1 이미지형 페이지

기본 후보 규칙은 다음과 같다. 값은 운영 설정이 아니라 파서 모듈의 상수로 시작하고, 실제 NAS
운영 지표를 근거로만 바꾼다.

```text
visible_chars < 80
and min(sum(image_bbox_area / page_area), 1.0) >= 0.20
    => kind=image_page
```

가장 큰 이미지 하나의 면적만 사용하지 않는다. 작은 이미지 조각 여러 개가 페이지 전체 내용을
구성할 수 있기 때문이다. `image_page`는 페이지 전체를 렌더링한다.

### 5.2 표

한 페이지의 벡터 도형은 한 번만 읽는다.

```python
drawings = page.get_drawings()
tables = page.find_tables(paths=drawings).tables
```

`find_tables()` 결과는 구조화 데이터로 확정하지 않고 표 영역을 찾는 데만 쓴다. 각 표 bbox의
로컬 텍스트와 렌더링 이미지를 외부 요청에 함께 넣는다.

### 5.3 차트

표 탐지에서 읽은 `drawings`를 다시 사용한다.

```python
regions = page.cluster_drawings(drawings=drawings)
```

너무 작거나 페이지 전체에 가까운 영역은 버린다. 표 bbox와 겹치는 차트 후보는 중복 요청하지
않고 더 구체적인 표 후보를 우선한다.

### 5.4 중복 제거

동일한 첨부·페이지에서 겹치는 bbox는 하나로 합친다. 렌더링한 결과의 SHA-256이 이미 처리된
크롭과 같으면 새 외부 요청을 만들지 않는다.

## 6. 외부 요청 계약

파싱 단계는 다음 매니페스트까지만 만든다.

```json
{
  "document_id": 0,
  "attachment_id": 0,
  "source_sha256": "...",
  "page": 1,
  "kind": "table|chart|image_page",
  "bbox_pdf_points": [0.0, 0.0, 595.3, 841.9],
  "crop_path": "...",
  "crop_sha256": "...",
  "local_text": "...",
  "status": "not_called",
  "dispatch_allowed": false
}
```

필수 규칙은 다음과 같다.

- 파싱 태스크는 외부 API 키를 읽지 않는다.
- 매니페스트와 로그에 API 키를 저장하지 않는다.
- `dispatch_allowed=false`인 요청은 dispatcher가 거부한다.
- 응답 병합 전에 현재 첨부 SHA와 `source_sha256`을 다시 비교한다.
- provider·model·prompt version·비용 필드는 실제 호출 구현과 함께 추가한다.
- 문서 중요도, 일일 비용 상한과 재시도 정책은 dispatcher가 담당한다.

실제 외부 호출을 붙이기 전까지 매니페스트 상태는 `not_called`에서 바뀌지 않는다.

## 7. 병합

페이지 텍스트에는 페이지 표식을 유지하고 외부 후보 위치에는 요청 ID를 남긴다.

```text
<!-- page:1 -->
로컬 추출 텍스트
[[EXTERNAL_ANALYSIS_PENDING id=ext-001 kind=table page=1]]
```

외부 결과가 생기면 같은 attachment·page·bbox의 표식을 분석 텍스트로 교체한다.

- `image_page`: 로컬 텍스트가 비었으면 외부 결과가 페이지 본문이 된다.
- `table`: 표 제목, 열 이름, 행 데이터와 단위를 보존한 텍스트를 넣는다.
- `chart`: 제목, 축, 범례, 기간, 주요 값과 추세를 넣는다.
- 외부 결과라는 출처와 요청 ID를 삭제하지 않는다.
- 같은 페이지의 결과는 bbox의 y축, x축 순서로 배치한다.

`PENDING` 표식이 남은 문서는 청크와 임베딩 단계로 보내지 않는다. 외부 분석을 사용하지 않기로
결정한 요청은 `skipped`로 확정한 뒤 진행한다.

## 8. 저장 계약

`document.body`는 HTML 본문의 원본 의미를 유지하고 PDF 추출 텍스트로 덮어쓰지 않는다.
첨부 파싱 결과는 `document_attachment`에 연결한다.

구현 마이그레이션에서 필요한 최소 정보는 다음과 같다.

| 값 | 용도 |
| --- | --- |
| 파싱 상태 | `NULL`은 미처리, 성공·외부 보강 필요·실패·미지원 구분 |
| 추출 텍스트 | 외부 결과까지 병합한 첨부 단위 본문 |
| 파싱 원본 SHA | 현재 첨부와 결과가 같은 파일에서 나왔는지 확인 |
| 파서 이름·버전 | 규칙 변경 시 재처리 대상 판정 |
| 파싱 시각 | timezone-aware UTC |
| 매니페스트 경로 | 페이지·단어·이미지·외부 요청 좌표 원본 |

정확한 컬럼 이름과 CHECK 제약은 파서와 마이그레이션을 같은 작업에서 구현할 때 확정한다.
읽는 코드 없이 NULL 컬럼만 먼저 추가하지 않는다.

`document_chunk`는 상위 설계의 `(document_id, position)` 자연키를 따른다. 청크에는 최소한 다음
메타데이터가 필요하다.

```json
{
  "document_id": 0,
  "attachment_id": 0,
  "position": 0,
  "page_from": 1,
  "page_to": 1,
  "content": "...",
  "content_hash": "...",
  "embedding_model": null,
  "embedded_at": null
}
```

## 9. Chunking·Embedding·RAG

청크는 병합이 끝난 첨부 텍스트만 대상으로 만든다.

- 크기 2,000자, overlap 200
- 문단과 페이지 경계를 우선
- 표 하나는 가능하면 같은 청크에 유지
- 청크마다 document·attachment·page 범위를 보존
- `content_hash`가 같으면 다시 임베딩하지 않음
- 임베딩 모델이 바뀌면 기존 벡터와 섞지 않음

Embedding은 별도 태스크다. 파싱 실패, 외부 분석 대기, 병합 실패 문서는 입력에서 제외한다.
VectorDB 적재 뒤에도 원문 PDF와 첨부 SHA까지 역추적할 수 있어야 한다.

## 10. 실패 처리

- PDF 열기 실패: 해당 첨부만 실패시키고 다른 첨부 처리는 계속한다.
- 특정 페이지 실패: page와 예외를 기록하고 첨부 전체를 성공으로 확정하지 않는다.
- 렌더링 실패: 텍스트는 보존하되 외부 요청은 만들지 않고 실패 원인을 남긴다.
- 원본 SHA 변경: 이전 파싱·외부 결과·청크를 사용하지 않고 다시 처리한다.
- 외부 호출 실패: 로컬 텍스트는 보존하고 `PENDING` 상태로 남겨 임베딩을 막는다.
- 결과 병합 실패: 원본 결과를 보존하고 자동으로 부분 병합하지 않는다.

실패한 첨부를 삭제하지 않는다. 다음 정시 실행이 다시 처리할 수 있도록 상태와 원인을 남긴다.

## 11. Synology NAS 운영 원칙

- Airflow pool 또는 DAG 동시 실행 수로 PDF 파싱을 1개만 허용한다.
- 첨부 한 개를 처리한 즉시 결과를 저장하고 메모리를 해제한다.
- 이미지 바이너리는 후보 판정 전에 읽지 않는다.
- 전체 페이지 렌더링은 이미지형 페이지에만 사용한다.
- 표·차트는 bbox만 렌더링한다.
- 기본 렌더링은 144 DPI·RGB·`alpha=False`다.
- OCR·로컬 Vision 모델·Docling은 설치하지 않는다.
- 로컬과 운영 Airflow requirements는 같은 PyMuPDF 버전 범위를 사용한다.
- 운영 Airflow의 Debian 계열 이미지를 유지하고 Alpine/musl로 바꾸지 않는다.
- 처리 시간, 최대 RSS, 후보 페이지 수, 호출 수와 비용을 실행마다 기록한다.
- NAS 컨테이너 이미지 빌드와 단일 PDF smoke check를 통과한 뒤 DAG를 켠다.

## 12. 구현 순서

1. 로컬·운영 Airflow requirements에 같은 PyMuPDF 버전 범위를 추가한다.
2. PDF 한 첨부를 순차 처리하는 파서와 페이지 산출물 모델을 구현한다.
3. 이미지형 페이지·표·차트 후보 판정과 렌더링을 구현한다.
4. 비활성 외부 요청 매니페스트를 생성한다.
5. 파싱 상태와 결과를 저장하는 마이그레이션·SQL을 구현한다.
6. 페이지 결과 병합과 Chunking을 구현한다.
7. NAS 컨테이너에서 CPU·RSS와 파일 권한을 확인한다.
8. 외부 Vision 제공처와 비용 정책이 확정된 뒤 dispatcher를 구현한다.
9. 외부 결과 병합이 안정된 뒤 Embedding과 VectorDB 적재를 구현한다.

## 13. 완료 조건

- PDF 일반 텍스트·페이지·이미지·단어 좌표를 원본 첨부 SHA와 함께 보존한다.
- 이미지형 페이지는 전체 페이지, 표·차트는 필요한 bbox만 렌더링한다.
- 파서가 외부 API 키나 네트워크 클라이언트를 사용하지 않는다.
- 모든 외부 요청은 기본 `not_called`·`dispatch_allowed=false`다.
- 첨부 SHA가 다른 외부 결과를 병합하지 않는다.
- 외부 분석 대기 또는 병합 실패 문서를 임베딩하지 않는다.
- 한 첨부의 실패가 다른 첨부 처리를 막지 않는다.
- NAS에서 파싱 작업이 한 개만 실행된다.
- 청크에서 원본 document·attachment·page·SHA까지 역추적할 수 있다.

## 14. 선행 문제

파서가 파일 내용을 정확히 읽어도 그 파일이 원래 문서에 붙었던 파일인지는 증명하지 못한다.
고정되지 않은 첨부 URL이 나중에 다른 파일을 반환할 수 있으므로 수집 시점의 SHA-256, 응답
메타데이터와 문서 ID 관계를 보존해야 한다. 같은 URL의 내용이 바뀌면 새 파일을 과거 문서에
조용히 덮어쓰지 않는다. 이 연관성 문제는 RAG 적재 전에 수집 단계에서 해결한다.
