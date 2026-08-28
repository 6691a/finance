---
name: writing-collectors
description: Use when adding, changing, or reviewing a data collector in airflow/modules/collectors/ — fetching from an external API, RSS feed, CSV, or HTML page and writing rows to Postgres. Covers 수집기 작성 규칙, fetch/store 분리, Pydantic 검증, SecretStr, 잘림 검사, 날짜 표기, scrapling. Also use when a provider returns a silent 0 rows, a truncated page, an unexpected identifier, or a locale-dependent date.
---

# 수집기 작성

`airflow/modules/collectors/` 아래의 수집기 하나를 더하거나 고칠 때 쓴다.

**기준 구현 둘을 먼저 연다.**

- `airflow/modules/collectors/analyst/kis_opinion.py` — KIS 토큰을 쥔 클래스
- `airflow/modules/collectors/indicator/fred.py` — API 키 하나를 쥔 클래스, **검증 규칙의 기준**

## 핵심 원리

**조용한 성공을 만들지 않는다.** 아래 규칙의 절반은 "제공처가 실패를 성공처럼 알려 줄 때
그것을 실패로 바꾸는 법"이다. 0건·잘린 응답·엉뚱한 식별자·모르는 날짜 표기는 전부
그 자리에서 터뜨린다. 나중에 알수록 되짚을 구간이 길어진다.

## 구조

| 규칙 | 이유 |
| --- | --- |
| **클래스로, 도메인 폴더에** (`market/`·`document/`·`indicator/`·`calendar/`·`analyst/`) | 자격 증명·토큰처럼 호출마다 안 변하는 값을 들고 돈다 |
| 하위 패키지 `__init__.py`는 **재수출하지 않는다** | 한 수집기의 의존성이 없는 환경에서 관계없는 DAG이 import 오류로 죽는다 |
| **`fetch`(외부 호출)와 `store`(DB 쓰기)를 나눈다** | DAG이 `fetch` 실패로 재시도를 판단하고 성공한 것만 트랜잭션 안에서 저장한다 |
| 생성자는 그 실행 동안 **안 변하는 것만** 받는다 | 종목·구간처럼 호출마다 바뀌는 것은 메서드 인자다 |

판정 기준과 "함수로 두는 것이 맞다고 판정한 모듈"은
[docs/convention/collectors-class-migration.md](../../../docs/convention/collectors-class-migration.md)에 있다.

## 데이터 모양

- **요청 값, 외부 응답 본문, 정규화 결과, 수집 결과를 전부 Pydantic 모델로 선언한다.**
  `dataclass`를 쓰지 않는다. 외부 JSON은 `model_validate_json`으로 검증한다.
- **모델은 `ConfigDict(frozen=True)`다.** 재시도 경로에서 값이 바뀌면 원본과 저장값이 어긋난다.
- 시각 필드는 `AwareDatetime`으로 받고 validator에서 UTC로 정규화한다.
- 허용 값이 정해진 필드는 validator로 막는다(예: 시계열 ID는 `TREASURY_SERIES` 안의 값만).

## 제공처가 거짓말할 때

**이 절이 이 스킬의 값어치다.** 각 항목은 실제로 한 번씩 터진 것이다.

- **잘못된 식별자에도 정상 응답으로 답하면 Enum으로 좁혀 요청 전에 막는다.**
  ECOS는 없는 항목코드에도 데이터 없음(`INFO-200`)으로 답해서 오타가 조용한 0건이 된다.
  `collectors/indicator/ecos.py`의 `EcosSeries`가 그 예다.
- **요청하지 않은 식별자가 섞여 오면 실패시킨다.** ECB는 `KEY` 칸을 매 행 대조해
  `G_N_C`(전체 발행자) 곡선이 AAA 곡선에 섞이는 것을 막는다. 조용히 같은 만기에 값이
  두 개 생기는 것보다 멈추는 편이 낫다.
- **값 없음과 잘못된 식별자를 같은 응답으로 알리면 조회 구간 앞에 패딩을 붙인다.**
  영업일이 반드시 들어가게 해서 둘을 가른다. BoE IADB는 둘 다 HTTP 200에 HTML 오류
  페이지다. `boe.FETCH_PADDING_DAYS`가 14일을 붙이고 저장은 구간 안만 한다. 패딩을
  붙이고도 오류 페이지면 식별자나 구간이 틀린 것이다.
  **반대로 둘이 갈리는 제공처에는 패딩을 붙이지 않는다** — ECB는 값 없음이 HTTP 200 빈
  본문, 없는 키가 404다.
- **페이지 단위로 잘릴 수 있으면 전체 건수와 받은 행 수를 대조한다.** 조용히 잘린 응답은
  조회 구간에 구멍을 남긴다. 제공처가 건수를 안 주면 상한에 닿은 것 자체를 실패로 만든다
  (`analyst/kis_opinion.py`의 `MAX_ROWS`).
- **표를 위치(index)로 읽으면 칸 수를 상수로 두고 응답마다 검증한다.** 사이트가 열을
  추가하면 값이 조용히 옆 칸으로 밀린다. CSV도 같고, **저장하지 않는 열까지 헤더 전체를
  대조한다** — 저장 대상만 확인하면 그 사고를 못 잡는다(`mof.EXPECTED_HEADER`).

## 날짜와 로케일

- **제공처가 자기 나라 표기로 주면 그 변환도 수집기가 한다. 모르는 표기는 실패시킨다.**
  재무성은 和暦(`R8.8.3` = 2026-08-03)을 쓴다.
- **로케일을 타는 표기는 표를 직접 둔다.** `strptime`/`strftime`의 `%b`·`%a`는 실행 환경의
  `LC_TIME`을 타므로 컨테이너 로케일이 바뀌면 조용히 실패한다. BoE의 `03 Aug 2026`은
  `boe.MONTH_NAMES`가 읽고 쓴다.
- **날짜 문자열은 모양을 먼저 보고 파싱한다.** `date.fromisoformat`은 `2026-W32` 같은 ISO
  주 표기도 받아 그 주의 월요일로 바꾼다. 주간·월간 값이 섞이면 조용히 엉뚱한 날짜가 된다.
  `ecb.ISO_DATE_PATTERN`이 달력 하루인지 먼저 본다.
- **조회 구간 계산은 `modules/period.py`에 한 벌만 둔다.** DAG마다 복사하지 않는다.
  이 모듈은 Airflow를 import하지 않는다 — 하면 수집기 테스트가 배포 환경 없이 못 돈다.
  실패는 `PeriodError`이고 `AirflowFailException`으로 바꾸는 건 DAG가 한다.

## 자격 증명과 요청

- **API 키는 `SecretStr`로 받는다.** URL에 키가 들어가므로 **예외 메시지와 로그에 URL을
  넣지 않는다.** ECOS는 질의 문자열이 아니라 URL 경로에 키를 받는데 규칙은 같다.
- **반대로 인증이 없는 제공처는 URL을 그대로 남긴다**(`mof.py`·`boe.py`·`ecb.py`).
  감출 게 없는데 감추면 디버깅만 어려워진다.
- 인증이 아니라 **차단을 피하려고** User-Agent를 명시하는 것은 별개다. 재무성과 BoE는
  기본 `Python-urllib/3.x`를 막는다.
- **HTML 수집은 scrapling이다.** 요청은 `Fetcher`(curl_cffi), 파싱은 `Selector`.
  `impersonate`로 브라우저 지문을 흉내 내 앞단 WAF에 안 막힌다(`documents.py`가 그 예다).
  **페이지가 JavaScript로 표를 그릴 때만** `DynamicFetcher`·`StealthyFetcher`를 쓴다 —
  브라우저를 띄우므로 기본값이 아니다.

## 오류를 위로 올리는 법

- **외부 오류는 재시도 가능 여부로 나눠 올린다. 판단은 DAG가 한다.**
- **제공처가 실패를 HTTP 상태가 아니라 본문으로 알리면 그 코드를 담는 예외를 따로 둔다**
  (`EcosResultError`). 수집기는 코드를 해석하지 않는다.
- 자세한 실패 판정 형태(항목별 실패 수집 / 태스크 매핑 / 단일 요청)는 `.claude/CLAUDE.md`의
  "오류 처리" 절에 있다.

## 수집 단위와 계보

- **수집 단위가 시계열이 아니라 파일이나 조회 한 번이면 `source_record`도 그 단위로 남긴다.**
  `source_key`에 파일 이름(`jgbcm`)이나 조회 이름(`gilt_nominal_par_yields`,
  `YC.B.U2.EUR.4F.G_N_A.SV_C_YM`)을 넣는다. 시계열마다 태스크를 매핑하지 않는다 —
  응답 하나가 곡선 전체를 담고 있어 나눠 요청할 것이 없다.
- **원본이 JSON이 아니면 `payload`에 넣지 않는다**(컬럼 타입이 jsonb). 대신 어느 파일이
  어느 구간을 담고 있었는지를 `metadata`에 남겨 재현할 수 있게 한다.
- **제공처가 파일을 여러 개로 쪼개 고시하면 어느 파일을 받을지도 수집 규칙이라 `modules/`가
  정한다.** 재무성은 이번 달치와 과거 전체를 따로 두고 둘이 안 겹쳐서, 구간이 달 경계를
  넘으면 둘 다 받아야 한다. `mof.fetch_curves`가 그 판단을 하고 DAG는 결과만 저장한다.

## 목록 → 상세 두 단계 수집

- **이미 있는 `(source_slug, external_id)`를 먼저 빼고 새 항목만 상세를 받는다**
  (`document_listings.ListingSource.enrich`, 네이버 리서치가 그 예다). 기존 항목을 목록
  정보로 다시 upsert하면 `content_hash`가 달라져 상세 요약이 지워지고 재평가가 돈다.
- **상세 HTTP는 트랜잭션 바깥에서 부른다.**
- **제공처가 관심 밖까지 밀어 주면 수집 단계에서 거른다.** 종목이 붙은 문서는
  `instrument.is_watched` 안의 것만 받는다(`documents.watched_tickers`). 거르기는 상세 요청
  **앞**이고, 거르기에 쓴 목록 값(`FeedItem.stock_code`)은 **저장하지 않는다** — 태그의
  원본은 LLM 평가가 만드는 `document_instrument`다. 종목이 없는 문서(시황·경제·채권)는
  시장 전체 이야기라 받고, 카테고리를 통째로 끄는 손잡이는 `document_source.enabled`다.
- **robots.txt가 일반 봇을 막는 출처를 사용자 결정으로 수집할 때**는
  `document_source.terms_url`·`terms_checked_at`과 시드 리비전 주석에 그 결정을 남긴다.
  이용조건이 문제가 되면 코드가 아니라 `enabled`를 내리는 것으로 끝나야 한다.

## 흔한 실수

| 실수 | 무엇이 터지나 |
| --- | --- |
| `dataclass`나 맨 dict로 응답을 받음 | 키 오타가 런타임까지 살고 프롬프트·JSONB로 그대로 나간다 |
| 모델을 mutable로 둠 | 재시도 경로에서 원본과 저장값이 어긋난다 |
| `except Exception`으로 감싸고 0건 반환 | Airflow가 성공으로 표시하고 다음 실행도 같은 자리에서 조용하다 |
| 예외 메시지에 URL을 실음 | 키가 로그로 샌다 |
| 저장할 열만 헤더 대조 | 사이트가 열을 추가하면 값이 옆 칸으로 밀린 채 저장된다 |
| 하위 `__init__.py`에서 재수출 | 관계없는 DAG이 import 오류로 죽고 `tests/modules/test_import_weight.py`가 깨진다 |

## 검증

수집기는 문자열 SQL을 쓰므로 **INSERT 컬럼과 `ON CONFLICT` 키를 모델 metadata와 대조하는
테스트를 함께 쓴다** — `tests/collectors/`의 `test_fred.py`·`test_ecos.py`·`test_mof.py`·
`test_boe.py`·`test_ecb.py`가 그 형태다.

```bash
uv run pytest tests -q
uv run ruff check apps airflow migrations tests
```
