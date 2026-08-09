# 개발 문서 — 1분봉 수집 25종 (해외 Yahoo · 국내 KIS)

> **상태: 구현 완료 (2026-08-08).**
>
> §3의 설계대로 구현했다. 추가된 파일은 다음과 같다.
>
> | 파일 | 역할 |
> | --- | --- |
> | `apps/models/market.py` | `QuoteBar` 모델 |
> | `migrations/versions/9c1faba10bf1_create_quote_bar_table.py` | 테이블 생성 |
> | `airflow/sql/postgres/quote_bar/upsert.sql` | 멱등 upsert |
> | `airflow/modules/collectors/yahoo.py` | 수집기 |
> | `airflow/dags/yahoo_quote_intraday.py` | 5분 폴링 DAG + 백필 |
> | `airflow/modules/collectors/kis.py` | KIS 수집기 (국내 지수·선물) |
> | `airflow/dags/kis_quote_intraday.py` | 국내 폴링 DAG |
> | `tests/collectors/test_yahoo.py` | Yahoo 수집기 테스트 28개 |
> | `tests/collectors/test_kis.py` | KIS 수집기 테스트 29개 |
> | `apps/models/reference.py` | `QuoteSymbol` 마스터 모델 |
> | `migrations/versions/05fb2529386d_create_quote_symbol_master.py` | 마스터 + 시드 |
> | `tests/migrations/test_quote_symbol_catalog.py` | Enum·시드 대조 12개 |
> | `compose/local/grafana/dashboards/quote-index.json` | 지수 대시보드 |
> | `compose/local/grafana/dashboards/quote-index-future.json` | 지수선물 대시보드 |
> | `compose/local/grafana/dashboards/quote-fx.json` | 환율 대시보드 |
> | `compose/local/grafana/dashboards/quote-commodity.json` | 원자재 대시보드 |
> | `compose/local/grafana/dashboards/quote-intraday.json` | 통합 대시보드 |
>
> 검증 결과는 §7, 백필은 §8, 대시보드는 §9, 국내 수집은 §10, 심볼 확장은 §11에 있다. §6은 설계 단계에서 확인한 저장소·도구
> 관련 사실들이다.
>
> **알아둘 제약 하나:** 백필한 봉의 `previous_close`는 신뢰할 수 없다. §8.3을 본다.

## 1. 배경

기존 수집(`fred_treasury_daily`, `ecos_market_rate_daily`)은 **일별 리포트**용이다.
`indicator_observation`에 날짜 단위 스칼라 1건씩 쌓고, "전일 상황이 이러하니 오늘 방향성은
이렇다"를 유추하는 데 쓴다.

이번 건은 목적이 다르다. **실시간 알림**이다. 미국 반도체 선물이 빠지면 한국 반도체
종목도 곧 빠질 수 있다는 신호를, **한국 장중을 포함해 하루 종일** 받으려는 것이다.
예: 한국 시간 14:00에 나스닥 선물이 급락하면 그 시점에 알림이 가고, 그걸 보고 한국
반도체 종목의 향방을 판단한다.

### 무엇이 언제 움직이는가

이 표가 설계 전체를 결정한다. 미국 정규장은 KST 22:30~05:00이므로 **한국 정규장
시간대에는 미국 현물 지수가 통째로 멈춰 있다.**

| 구간 (KST) | 미국 선물<br>`ES=F` `NQ=F` | 미국 현물<br>`^SOX` `^VIX` | 코스피<br>`^KS11` | **코스피200 선물**<br>`A016xx` |
| --- | --- | --- | --- | --- |
| **09:00~15:30 한국 정규장** | **거래 중** | 멈춤 | 거래 중 | **거래 중** |
| 15:30~15:45 | 거래 중 | 멈춤 | 멈춤 | **거래 중** |
| 15:45~22:30 | 거래 중 | 멈춤 | 멈춤 | 멈춤 |
| 22:30~05:00 미국 정규장 | 거래 중 | 거래 중 | 멈춤 | 멈춤 |
| 05:00~06:00 | 거래 중 | 멈춤 | 멈춤 | 멈춤 |
| **06:00~07:00** | **CME 정비 휴장** | 멈춤 | 멈춤 | 멈춤 |
| 07:00~09:00 | 거래 중 | 멈춤 | 멈춤 | 멈춤 |

**한국 정규장 구간에 살아 있는 건 선물과 아시아 시장, 그리고 환율이다.** 미국 현물은 그
시간 내내 멈춰 있다. 그 구간을 채우는 값을 순서대로 붙여 왔다.

| 무엇 | 한국 정규장 구간 |
| --- | --- |
| 미국 선물 · 코스피200 선물 | 거래 중 — 이 수집의 출발점(§9.3) |
| 환율 5종 · 원자재 4종 · 미 국채선물 | 거의 24시간 움직인다(§11) |
| 닛케이·대만·항셍·상하이 | 시간대가 겹치는 해외 현물(§11) |
| 미국 현물(SOX·VIX·러셀), 미 10년물 **금리** | **멈춤** |

마지막 줄이 중요하다. `US10Y`(수익률)는 미국 정규장에만 움직여서 한국 장중에는 죽어 있다.
그 구간의 미 금리 신호는 `US10Y_FUT`(국채선물 가격)뿐이다(§11.3).

선물 정규장은 15:45까지다. 주식(15:30)보다 15분 늦다. 주말은 선물도 쉰다
(미국 선물은 토 06:00 ~ 월 07:00 KST). 미국 서머타임 기준이고 겨울에는 한 시간씩 밀린다.

### 여기서 나오는 제약 셋

- **그레인이 분이다.** 날짜 단위 테이블로는 못 담는다. 새 테이블이 필요하다.
- **선물이어야 한다.** 한국 장중에 살아 있는 미국 신호는 선물뿐이다. `^SOX`는 현물이라
  한국 정규장 내내 멈춰 있다. 반도체 방향의 실시간 대리 지표는 `NQ=F`다.
  같은 이유로 한국 쪽 반응도 현물(`^KS11`)이 아니라 **코스피200 선물**이 더 빠르다.
- **수집은 24시간 돈다.** 미국 장 시간에만 도는 스케줄로는 한국 장중을 놓친다.
  대신 "지금은 아무것도 안 움직이는 구간"이 상시 존재하므로, **조용한 구간을 오류로
  착각하지 않는 것**이 수집기의 핵심 요구사항이 된다(§3.4).

**이번 범위는 수집까지다.** 알림 임계값은 데이터가 며칠 쌓여 실제 변동폭 분포를 본 뒤
정한다. 지금 정하면 근거 없이 찍는 숫자가 된다.

## 2. 수집 소스 결정

**국내는 KIS, 미국은 Yahoo다.** 한 소스로 통일하지 못한 이유가 이 절의 내용이다.

| 대상 | 소스 | 왜 |
| --- | --- | --- |
| 코스피·코스피200·코스피200 선물 | **KIS** | 국내 시세는 무료다. 코스피200 선물은 Yahoo에 아예 없다 |
| 미국·아시아 지수, 미국 선물, 환율, 금리 | **Yahoo** | KIS의 CME **API** 시세료가 월 USD 221.10이다 |

**"국내에서 받을 수 있는 것은 국내를 우선한다"** 가 원칙이다. KIS는 공식 API이고 국내
시세는 무료다. Yahoo는 비공식이라 언제 막힐지 모르고 품질도 들쭉날쭉하다(§8.4).
해외 쪽이 Yahoo인 이유는 순전히 비용이다.

**코스피는 처음에 Yahoo(`^KS11`)로 받다가 KIS로 옮겼다.** 그 이유가 §8.4에 있다.

한국투자증권 API를 먼저 검토했다(계정 보유). `notebooks/kis_probe.ipynb`로 실제 계정에서
호출해 확인했고, 시세 신청 전이라 프로브 자체는 비용이 0원이었다.

### 2.1 KIS 프로브 결과

| 확인 | 결과 |
| --- | --- |
| 해외지수 분봉 (`FHKST03030200`) | **SPX·VIX·SOX·NDX·COMP 전부 조회됨.** 시세 신청 없이 무료 |
| 해외선물 (`HHDFC55010000` / `HHDFC55020400`) | **HTTP 500 · `EGW00550` "CME SUB거래소 신청 계좌가 아닙니다."** |
| 응답 시간대 | **미국 동부 기준** (SPX 마지막봉 `16:19` = 정규장 마감 직후) |
| 지수 분봉 신선도 | **장중에 갱신되지 않음** |

- 선물 500은 월물 코드 형식 문제가 아니다. 루트만 넣은 `ES`도, 마이크로 `MESU26`도 같은
  오류다. 반면 `CNHU26`·`CNHZ26`·`BONU26`은 HTTP 200에 실제 시세가 왔다.
  **호출 경로는 정상이고 CME SUB거래소만 막혀 있다.**
- 지수 분봉 신선도는 두 번 실행해 확인했다.

  | 실행 시점 | 마지막 봉 |
  | --- | --- |
  | EDT 8/7 11:08 (장중) | 8/6 16:19 |
  | EDT 8/7 21시경 (마감 후) | 8/7 16:50 |

  하루 지연은 아니지만 **장중에는 당일 데이터가 보이지 않는다.** 당일 알림이 목적이므로
  이 경로는 탈락이다. `FID_HOUR_CLS_CODE=1`(시간외)은 0봉이다.
- **봉 배열이 역순(최신순)이다.** 첫 원소 `165000`, 마지막 `144000`.

### 2.2 비용

API 시세료는 HTS/MTS와 별개다.

| 경로 | CME 시세료 |
| --- | --- |
| HTS/MTS | 무료 |
| **API** | **월 USD 221.10** (2025 기준), 2026-01-01 추가 인상 |

공지에 "API를 사용하여 CME Group의 시세를 수신하는 경우"에만 발생하고 "HTS/MTS 사용
고객은 해당사항없음"이라 명시돼 있다. 하위거래소(CME·CBOT·COMEX·NYMEX)마다 붙는다.

[CME API 시세비용 인상 안내](https://securities.koreainvestment.com/main/customer/notice/Notice.jsp?cmd=TF04ga000002&currentPage=1&num=43267) ·
[CME API 및 ICE거래소 시세비용 인상의 건(2026)](https://m.koreainvestment.com/main/customer/notice/Notice.jsp?cmd=TF04ga000002&num=45891)

### 2.3 결정: 미국은 Yahoo Finance

**미국 선물이 막혀 있는 한 Yahoo가 필요하고, Yahoo를 쓸 바에는 미국 지수도 Yahoo로
통일하는 게 싸다.** 미국 것만으로 수집기를 두 벌 유지하는 비용이 KIS 지수가 주는 이점보다
크다. (국내는 이야기가 다르다. §10을 본다.)

Yahoo v8 chart 실측 결과:

- `/v8/finance/chart/{symbol}?interval=1m&range=1d` → **200**, 1분봉 배열 통째로
  (ES=F 615봉, ^KS11 361봉). 5분마다 호출해도 저장 그레인은 1분이다.
- `/v7/finance/quote` 배치는 **401**로 잠겨 있다. 심볼마다 한 번씩 부른다.
- 기본 `urlopen`/curl은 **429**. 이미 설치된 `scrapling`의 `Fetcher`에
  `impersonate="chrome"`을 주면 200. `hana.py`와 같은 도구·같은 설정이고 새 의존성이 없다.
- `meta.chartPreviousClose`가 전일종가를 준다. 알림 변동률의 분모다.

수집 대상 5종 전부 200 확인:

| 저장 `symbol` | Yahoo | 종류 |
| --- | --- | --- |
| `SP500_FUT` | `ES=F` | 지수선물 |
| `NASDAQ100_FUT` | `NQ=F` | 지수선물 |
| `VIX` | `^VIX` | 지수 |
| `SOX` | `^SOX` | 지수 |
| `KOSPI` | `^KS11` | 지수 |

**Yahoo는 비공식 API다.** 키도 비용도 없지만 언제든 막힐 수 있다. 저장 계약이 `provider`로
갈라져 있어 교체 범위는 수집기 한 파일이다.

### 2.4 알아둘 것 — 반도체 신호는 `NQ=F`가 대리한다

**`^SOX`는 선물이 아니라 현물 지수다.** 소매로 접근 가능한 SOX 선물은 없다. §1의 표대로
`^SOX`는 한국 정규장 시간 내내 멈춰 있으므로, "한국 시간 14:00에 반도체 선물이 빠졌다"를
잡아내는 값이 될 수 없다.

그 역할은 **`NQ=F`(나스닥100 선물)**가 한다. 나스닥100은 반도체 비중이 커서 실무적으로
반도체 방향의 대리 지표로 쓰인다. `^SOX`는 "어젯밤 미국 반도체가 어떻게 끝났나"의 기록,
`NQ=F`는 "지금 어디로 가고 있나"의 실시간 신호로 나눠 쓴다.

반도체만 더 정확히 보려면 `SOXL`·`SMH` ETF를 Enum에 한 줄 추가한다. 다만 ETF도 현물이라
프리마켓·애프터마켓(KST 17:00~22:30, 05:00~09:00)에만 움직이고 한국 정규장 시간에는
역시 멈춘다. **한국 장중 구간을 실제로 채우는 건 선물뿐이다.**

### 2.5 남은 변수

미국 쪽을 KIS로 옮길지 판단할 때만 필요한 것들이다. **지금 구현을 막지 않는다.**

| | 내용 |
| --- | --- |
| **프로브 F** | KIS 해외지수 현재가(output1)가 실시간인지. 미국 정규장 중에만 판정된다. 노트북에 셀이 남아 있다 |
| **CME 신청 금액** | `EGW00550`은 "신청하면 열린다"는 뜻이다. HTS `[7936]` 화면에 실제 청구액이 나온다 |

CME가 싸게 나오면 미국도 KIS로 옮기는 걸 재검토하고, 그 경우에만 F가 필요해진다.
테이블·SQL·대시보드는 어느 쪽이든 그대로 쓰인다. `provider` 컬럼이 그 교체를 흡수한다.

---

## 3. 설계

### 3.1 새 테이블 `quote_bar` — `apps/models/market.py`

`IndicatorObservation` 아래에 `QuoteBar`를 추가한다. `EntityBase` 상속.

> **주의:** `table_options`에는 `schema` 파라미터가 없다(`apps/core/database.py:55`).
> CLAUDE.md 설명과 달리 실제 코드는 스키마를 지정하지 않고 연결의 `search_path`를 따른다.
> `IndicatorObservation`도 그렇다. 인자는 `comment`, `database`, `managed`뿐이다.

| 컬럼 | 타입 | Null | 비고 |
| --- | --- | --- | --- |
| `provider` | `Text` | N | `yahoo` |
| `symbol` | `Text` | N | 저장 식별자(`SP500_FUT` 등). 제공처 안에서만 고유 |
| `bar_at` | `DateTime(timezone=True)` | N | 1분봉 시작 시각(UTC) |
| `open`/`high`/`low`/`close` | `Numeric(18,8)` | N | `indicator_observation.value`와 같은 정밀도 |
| `volume` | `BigInteger` | Y | 지수 선물은 0으로 오기도 한다. 없으면 NULL |
| `previous_close` | `Numeric(18,8)` | N | 직전 정규장 종가 |
| `source_record_id` | `BigInteger` FK RESTRICT | N | `indicator_observation`과 같은 계보 규칙 |

제약과 인덱스:

- `UniqueConstraint("provider", "symbol", "bar_at", name="uq_quote_bar_natural_key")`
  폴링 주기보다 넓은 구간을 받아도 겹치는 봉은 갱신으로 흡수된다.
- `Index("ix_quote_bar_source_record_id", "source_record_id")` — 기존 테이블과 같은 관례.
- **조회용 인덱스는 추가하지 않는다.** 알림 쿼리
  (`WHERE provider=? AND symbol=? ORDER BY bar_at DESC LIMIT n`)를 자연키 인덱스가 그대로 태운다.

설계 판단 둘:

- `previous_close`는 봉마다 같은 값이 반복되는 **의도적 비정규화**다. ES=F는 거의 23시간
  연속 거래라 세션 경계를 코드로 계산하려면 거래일 캘린더가 필요하다. Yahoo가 이미
  `meta.chartPreviousClose`로 주는 값을 그대로 박는 게 훨씬 싸다.
  `# ponytail: 봉마다 반복 저장. 세션 경계 계산이 필요해지면 그때 분리한다`
  **단 이 값은 실시간 폴링 구간에서만 맞다.** 백필한 구간에서는 요청 하나에 값이 하나뿐이라
  여러 날에 같은 값이 박힌다. 조회 쪽이 이걸 어떻게 우회하는지는 §8.3과 §9.3에 있다.
- **심볼 마스터 테이블은 처음에 만들지 않았다.** `reference.indicator_series`가 존재하는
  이유는 "조회 쪽을 안 고치려고"인데 그때는 조회 쪽이 없었다. 대시보드가 붙으면서 만들었다.
  §9.2를 본다.

`apps/models/__init__.py`의 `__all__`에 `QuoteBar`를 넣는다. 빠뜨리면 autogenerate가 못 본다.

`open`은 PostgreSQL 비예약어라 따옴표 없이 컬럼명으로 쓸 수 있다(DDL 컴파일로 확인).

### 3.2 마이그레이션

```bash
uv run python -m migrations.cli upgrade head
uv run python -m migrations.cli revision --autogenerate -m "create quote bar table"
```

- `just`는 `justfile`이 powershell로 설정돼 있어 macOS에서 안 돈다. 위 명령을 직접 쓴다.
- 생성된 파일을 **반드시 읽는다.** autogenerate가 `info={'database': ..., 'managed': ...}`를
  남기는데 다른 리비전에는 없는 노이즈다(DDL에 영향 없음). 지운다.
- 리비전은 pre-commit `exclude`에 걸려 자동 포맷되지 않는다. `ruff format`을 직접 돌려
  기존 리비전의 큰따옴표 스타일에 맞춘다.
- 스키마를 새로 만들지 않으므로 `CREATE SCHEMA`는 필요 없다.

### 3.3 `airflow/sql/postgres/quote_bar/upsert.sql`

`indicator_observation/upsert.sql`을 그대로 본뜬다.

```sql
INSERT INTO quote_bar (
    provider, symbol, bar_at, open, high, low, close, volume, previous_close, source_record_id
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (provider, symbol, bar_at) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low, close = EXCLUDED.close,
    volume = EXCLUDED.volume, previous_close = EXCLUDED.previous_close,
    source_record_id = EXCLUDED.source_record_id, updated_at = now()
```

`source_record/insert.sql`은 그대로 재사용한다.

### 3.4 `airflow/modules/collectors/yahoo.py`

`fred.py` 구조를 따른다. `Cursor`/`Connection` Protocol, frozen Pydantic 모델,
`read_sql`로 SQL 로드.

**`QuoteSymbol(StrEnum)`** — `ecos.py`의 `MarketRateSeries`와 같은 `__new__` 패턴으로
`(저장 symbol, yahoo_symbol, label)`을 한 줄에 묶는다. 심볼 추가는 여기 한 줄이면 끝난다.
저장 컬럼은 `Text`로 두고 허용 값은 이 Enum이 막는다(`series_id`와 같은 이유로 CHECK 없음).

**`fetch_bars(symbol) -> YahooResponse`** — `Fetcher.get(url, impersonate="chrome",
timeout=30)`. `CurlError`는 `ConnectionError`로 바꿔 재시도 가능하게 올린다. 비-2xx는
`YahooHTTPError(status)`. URL에 비밀이 없으므로 `hana.py`처럼 예외 체인을 유지한다
(`fred.py`는 키가 URL에 있어서 끊는다).

**`parse_bars(body, since=None) -> tuple[QuoteBar, ...]`** — `model_validate_json`으로 검증.

- **배열 길이를 먼저 대조한다.** `timestamp`와 `open`/`high`/`low`/`close` 길이가 어긋나면
  실패시킨다. 위치로 읽는 데이터라 조용히 밀리면 값이 통째로 틀어진다. `hana.py`가 표의
  칸 수를 검증하는 것과 같은 이유다. `volume`은 빠질 수 있어 있을 때만 본다.
- 값이 `None`이거나 `NaN`인 봉(거래 없던 분)은 건너뛴다. FRED의 `.`과 같은 취급이다.
- `Decimal(repr(value))`로 변환한다. float을 그대로 넣으면 이진 부동소수 오차가 실린다.
- `since`보다 이른 봉은 버린다. `range=1d`가 600봉 넘게 주는데 매번 전부 쓰면 폴링마다
  수천 건의 no-op UPDATE가 난다.
- epoch → `datetime.fromtimestamp(ts, UTC)`.

#### 조용한 구간과 고장을 구분한다

**이게 이 수집기에서 가장 틀리기 쉬운 지점이다.** 24시간 도는 수집이라 "새 봉이 없는"
상황이 상시 발생한다. §1의 표를 보면 하루의 상당 부분이 그렇다.

| 상황 | 언제 | 어떻게 나타나는가 |
| --- | --- | --- |
| CME 정비 휴장 | 매일 06:00~07:00 KST | 선물에 새 봉 없음 |
| 주말 | 토 06:00 ~ 월 07:00 KST | 선물에 새 봉 없음 |
| 미국 현물 마감 | 한국 정규장 포함 하루 대부분 | `^SOX`·`^VIX`에 새 봉 없음 |
| 한국 현물 마감 | 하루 대부분 | `^KS11`에 새 봉 없음 |
| **Yahoo 차단·심볼 폐지** | 언제든 | **실제 고장** |

**따라서 판정을 두 단계로 나눈다.**

1. **응답 자체가 비어 있으면 실패**(`YahooPayloadError`). `chart.result`가 없거나
   `timestamp` 배열이 통째로 비어 있는 경우다. Yahoo가 막히거나 심볼이 폐지되면 200에
   빈 배열이 온다. 이걸 성공으로 넘기면 조용한 구멍이 남는다. `ecos.py`가 `INFO-200`을
   다루는 것과 같은 취지다.
2. **`since` 필터 결과가 0건인 것은 정상**이다. 봉은 있는데 최근 `lookback_minutes` 안에
   없다는 뜻이고, 그건 위 표의 휴장 구간이다. 빈 튜플을 돌려주고 `SymbolOutcome`에
   `bar_count=0`으로 남긴다. **오류가 아니다.**

이 구분을 안 하면 매일 1시간, 주말 내내, 그리고 한국 정규장 시간의 `^SOX`마다 DAG가
실패한다. 알림 시스템이 스스로 노이즈가 된다.

부작용으로 "정말 막혔는데 조용히 0건"인 경우를 놓치지 않으려면, 응답의 마지막 봉 시각을
`SymbolOutcome`에 함께 남긴다. 그 값이 며칠씩 안 움직이면 사람이 보고 안다.

**`store_bars(connection, responses, lookback_minutes, failures=()) -> (int, tuple[SymbolOutcome, ...])`**

- **여러 심볼의 응답을 한 번에 받는다.** 폴링 1회 = `source_record` 1건이다. 심볼마다
  만들면 5분 주기로 영원히 도는 수집에서 계보 테이블이 수집 자체보다 빨리 커진다.
  CLAUDE.md가 웹소켓에 대해 정한 "배치 단위로 남긴다"와 같은 판단이다.
- 파싱을 먼저 끝내고 쓴다. 심볼 하나가 깨져도 나머지는 저장하고 사유를 metadata에 남긴다.
- `source_record`: `source_type="api"`, `source="yahoo"`, `source_key="intraday_1m"`,
  `record_count`=저장한 총 봉 수, **`payload=None`**, `metadata`={심볼별 결과, `lookback_minutes`}
- **`payload`를 저장하지 않는다.** 5분마다 5개 × 40KB면 하루 57MB다. CLAUDE.md가 대용량
  원본의 payload 생략을 허용한다.
- 트랜잭션 경계는 호출자(DAG)가 정한다.

### 3.5 `airflow/dags/yahoo_quote_intraday.py`

`fred_treasury_daily.py`의 `doc_md=__doc__` 형식을 따른다.

- **`schedule="*/5 * * * *"` — 요일·시간 제한을 두지 않는다.** 한국 장중에 미국 선물이
  움직이는 걸 잡는 게 목적이므로 미국 장 시간에만 도는 스케줄로는 안 된다. 주말과 CME
  정비 시간에도 그냥 돈다. 그 구간은 §3.4의 규칙에 따라 0건으로 조용히 지나간다.
  창을 좁혀 최적화하지 않는다. 요청 5개는 싸고, 시간 창 조건은 서머타임 전환과 임시
  휴장마다 틀리기 시작한다.
  `# ponytail: 24시간 무조건 폴링. 조용한 구간은 수집기가 0건으로 흡수한다`
- `catchup=False`, `max_active_runs=1`,
  `default_args={"retries": 1, "retry_delay": timedelta(minutes=2)}`.
  5분 주기라 실패해도 다음 run이 곧 덮는다. FRED의 1시간 간격 2회 재시도는 여기 안 맞는다.
- `start_date`는 `pendulum.datetime(..., tz=KST_TIMEZONE)` (`modules/utility.py`).
- **태스크는 하나다. `expand`하지 않는다.** fred/ecos는 시계열마다 매핑하지만 이 DAG는
  5분마다 영원히 돈다. 5개 매핑이면 하루 1440 task instance, 단일 태스크면 288이다.
  Airflow 메타데이터 DB에 그대로 쌓이는 차이다. 대신 심볼마다 `try/except`로 감싸 하나가
  실패해도 나머지를 저장하고, 전부 실패하면 태스크를 실패시킨다.
  `# ponytail: 단일 태스크. 심볼이 수십 개로 늘면 그때 expand로 나눈다`
- params: `lookback_minutes`(기본 `15`) 하나. 재시작·일시 장애 후 구멍을 메우려면 키운다.
  `fred_treasury_daily`의 `lookback_days`와 같은 장치다.
- 실패 분류:

  | 상황 | 처리 |
  | --- | --- |
  | HTTP 400/401/403/404 | `AirflowFailException` (설정 오류, 재시도 무의미) |
  | HTTP 429 | 그대로 올려 재시도. Yahoo는 비공식이라 정상 운영 범위 |
  | 그 밖의 HTTP·네트워크 오류 | 그대로 올려 재시도 |
  | 응답이 비어 있음 / 파싱 계약 위반 | `AirflowFailException`, 아무것도 쓰지 않음 |
  | **최근 구간에 새 봉이 0건** | **성공.** 휴장 구간이다(§3.4). `source_record`만 남는다 |

  마지막 줄이 핵심이다. **모든 심볼이 0건인 run도 성공이다.** 주말에는 그게 정상이다.
- 연결은 `PostgresHook(postgres_conn_id="news")`. `commit`/`rollback`/`close`는
  `fred_treasury_daily.collect`와 같은 형태로 DAG가 잡는다.
- `tags=["yahoo", "market", "intraday"]`

### 3.6 `tests/collectors/test_yahoo.py`

`tests/collectors/test_fred.py`의 헬퍼(`inserted_columns`, `required_columns`,
`placeholder_count`)를 같은 형태로 쓴다. 고정 JSON 픽스처와 가짜 `Connection`/`Cursor`를
쓰고 네트워크는 타지 않는다.

- SQL의 INSERT 컬럼이 `QuoteBar` 모델 metadata와 맞는지, `ON CONFLICT`가 자연키와 같은지
- `timestamp`와 quote 배열 길이가 어긋난 응답을 거부하는지
- `close=None`·`NaN` 봉을 건너뛰고 나머지를 저장하는지
- 값이 `Infinity`면 거부하는지
- epoch가 UTC로 정규화되는지
- 폴링 1회에 `source_record`가 **1건만** 생기고 `payload`가 `None`인지
- 심볼 하나가 실패해도 나머지가 저장되고 실패가 metadata에 남는지

**조용한 구간과 고장의 구분**(§3.4)이 이 수집기의 핵심이므로 두 방향을 다 고정한다.

- `timestamp`가 통째로 빈 응답은 **실패**로 만드는지 (Yahoo 차단·심볼 폐지)
- 봉은 있는데 전부 `since`보다 이르면 **빈 튜플을 정상 반환**하는지 (휴장 구간)
- 그때 upsert가 한 번도 호출되지 않고 `SymbolOutcome.bar_count == 0`으로 남는지
- **모든 심볼이 0건인 폴링도 성공으로 끝나는지** — 주말 시나리오다. 여기서 예외가 나면
  주말 내내 DAG가 빨갛게 된다

---

## 4. 검증

```bash
uv run python -m migrations.cli upgrade head
uv run python -m migrations.cli revision --autogenerate -m "verify"   # 빈 diff여야 한다
uv run pytest tests -q
uv run ruff check apps core dags migrations tests
```

빈 diff 확인용 리비전은 확인 후 지운다. `op.` 호출이 0개이고 전부 `pass`여야 한다.

DAG 실동작:

```bash
airflow dags test yahoo_quote_intraday
```

```sql
SELECT symbol, count(*), min(bar_at), max(bar_at), max(close)
FROM quote_bar GROUP BY symbol ORDER BY symbol;

-- 폴링 1회당 source_record 1건이고 payload가 비어 있는지
SELECT id, source_key, record_count, payload IS NULL, metadata
FROM source_record WHERE source = 'yahoo' ORDER BY id DESC LIMIT 3;
```

두 번 연속 돌려 멱등인지 확인한다. `count(*)`가 겹친 구간만큼 늘지 않아야 한다.
`bar_at`이 UTC인지(미국 정규장이 UTC 14:30~21:00에 걸리는지) 눈으로 확인한다.

### 로컬 환경 주의

- `config.yaml`이 `migration_owner`·`market_reader` 롤을 선언하는데 로컬 DB 초기화
  스크립트가 없어 롤이 만들어지지 않는다. 없으면 `market_migration` alias에서
  `InvalidPasswordError`로 막힌다. 한 번 만들어 두면 된다.
- `just`는 powershell 전용이라 macOS에서 안 돈다.

---

## 5. 이번 범위 아님

- **알림**: 데이터가 며칠 쌓이면 `(close - previous_close) / previous_close` 분포를 보고
  임계값을 정한다. 중복 발송 억제 규칙도 그때 같이 정한다. 설계할 때 §1의 표를 다시 본다.
  전일종가 대비 누적 변동뿐 아니라 **최근 N분 변화율**도 필요할 것이다. "한국 시간 14:00에
  빠졌다"를 잡으려면 그 시점의 급변을 봐야 하는데, 누적 변동률은 이미 아침부터 빠져 있던
  경우와 방금 빠진 경우를 구분하지 못한다. `quote_bar`에 분봉이 다 있으므로 조회로 풀린다.
  또 한국 정규장(09:00~15:30 KST) 안에서 오는 알림은 바로 매매 판단으로 이어지므로
  그 밖의 시간대와 발송 정책을 다르게 둘 수 있다.
- **Yahoo가 막히는 경우**: 수집기 한 파일만 교체한다. 후보는 stooq(무료 CSV), 또는 CME
  시세료를 감수한다면 KIS.

### KIS로 전환하게 될 경우 (참고)

CME가 싸게 나와 KIS로 가면 위 설계에서 바뀌는 부분만:

- `modules/collectors/kis.py`. scrapling 대신 stdlib `urlopen` (WAF 없음)
- 토큰은 DAG가 발급·캐시(Airflow Variable, 24h, 발급 횟수 제한)하고 수집기는 인자로 받는다.
  `fred.py`가 `api_key: SecretStr`을 받는 것과 같은 형태
- `quote_bar`에 `contract_code` 컬럼 추가(선물의 실제 월물). 월물이 바뀌면 가격에 갭이
  생기는데, 이 컬럼이 없으면 그 갭이 시장 급변인지 롤오버인지 구분할 수 없다
- **월물 롤오버를 하드코딩하지 않는다.** `inquire_price` 응답의 `expr_date`(만기일),
  `trd_to_date`(최종거래일), `remn_cnt`(잔존일수)를 API가 준다
- **봉 배열을 뒤집는다.** KIS는 최신순, Yahoo는 오름차순이다
- **오류 판정을 HTTP 상태나 JSON 파싱에 걸지 않는다.** KIS는 실패를 500으로 내면서 본문에
  `{rt_cd:"1",...}`처럼 키에 따옴표가 없는 비표준 JSON을 담는다. 표준 파서가 실패하므로
  본문에서 `msg_cd`를 문자열로 긁는다
- 값이 `"         6.7277"`처럼 공백 패딩돼 온다. `Decimal` 변환 전에 `strip()`한다

---

## 6. 시험 구현으로 검증한 사실

설계를 검증하려고 §3을 한 번 끝까지 만들어 돌려 본 뒤 되돌렸다. 아래는 그때 **실제로
확인한** 것들이라 구현할 때 다시 부딪히지 않아도 된다.

### 저장소·도구

| 사실 | 근거 |
| --- | --- |
| `table_options`에 `schema` 파라미터가 **없다** | `apps/core/database.py:55`. 인자는 `comment`·`database`·`managed`뿐이고 스키마는 연결의 `search_path`를 따른다. CLAUDE.md의 `table_options(schema=...)` 예시는 실제 코드와 다르다 |
| 테이블이 `public`에 생긴다 | 따라서 조회 SQL은 `market.quote_bar`가 아니라 `quote_bar`다 |
| `just`가 macOS에서 안 돈다 | `justfile:1`이 `set shell := ["powershell", ...]`. `uv run python -m migrations.cli ...`를 직접 쓴다 |
| 리비전은 pre-commit 포맷 대상이 아니다 | `.pre-commit-config.yaml`의 `exclude`에 `/migrations/`가 있다. `ruff format`을 직접 돌려야 기존 리비전의 큰따옴표 스타일에 맞는다 |
| autogenerate가 `info={...}`를 남긴다 | 다른 리비전에는 없는 노이즈다. DDL에는 영향이 없으니 지운다 |

### 스키마

| 사실 | 근거 |
| --- | --- |
| `open`을 컬럼명으로 써도 된다 | PostgreSQL 비예약어다. `CreateTable` 컴파일 결과에 따옴표가 붙지 않는다 |
| §3.1 스키마가 그대로 생성된다 | 마이그레이션을 실제로 적용해 테이블·주석·제약·인덱스를 `\d+`로 확인했다 |
| 모델과 DB가 일치한다 | 적용 후 `--autogenerate`를 한 번 더 떠서 `op.` 호출 0개(전부 `pass`)를 확인했다 |

### 로컬 환경

`config.yaml`이 `migration_owner`·`market_reader` 롤을 선언하는데 DB 초기화 스크립트가 없어
로컬에는 롤이 없다. 그러면 `market_migration` alias에서 `InvalidPasswordError`로 막힌다.
한 번 만들어 두면 된다.

```sql
CREATE ROLE migration_owner LOGIN PASSWORD 'migration_owner';
CREATE ROLE market_reader LOGIN PASSWORD 'market_reader';
GRANT ALL ON DATABASE news2 TO migration_owner;
GRANT ALL ON SCHEMA public TO migration_owner;
GRANT USAGE ON SCHEMA public TO market_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO market_reader;
```

`market_migration` alias는 그동안 한 번도 마이그레이션된 적이 없어서, 롤을 만들고 나면
밀려 있던 리비전이 한꺼번에 적용된다. 이 alias가 소유한 테이블은 없으므로 실제로 생기는
것은 리비전 포인터 테이블(`alembic_version_market_migration`)뿐이다.

### Yahoo

| 사실 | 근거 |
| --- | --- |
| `Fetcher.get(impersonate="chrome")`이 200을 받는다 | `stealthy_headers=True`도, 둘을 같이 줘도 200이다. `hana.py`와 같은 설정인 `impersonate`로 통일한다 |
| `interval=1m&range=1d`가 하루치 1분봉을 준다 | ES=F 615봉, ^KS11 361봉 |
| 5종 전부 조회된다 | `ES=F`·`NQ=F`·`^VIX`·`^SOX`·`^KS11` |
| `v7/finance/quote` 배치는 401이다 | 심볼마다 한 번씩 부를 수밖에 없다 |

---

## 7. 구현 검증 결과 (2026-08-08)

### 자동 검사

| 검사 | 결과 |
| --- | --- |
| `pytest tests` | **205 passed** (신규 22개 포함) |
| `ruff check apps airflow migrations tests` | 통과 |
| `pyrefly check` | 0 errors |
| 마이그레이션 적용 후 재-autogenerate | 빈 diff |
| Airflow DagBag 로드 | `import 오류: 없음`. 4개 DAG 전부 정상 |

### 실제 Yahoo 응답으로 확인

5개 심볼 전부 조회되고 파싱된다.

| 심볼 | 봉 수 | 구간(UTC) |
| --- | --- | --- |
| `SP500_FUT` | 1012 | 04:09 ~ 20:59 |
| `NASDAQ100_FUT` | 1012 | 04:09 ~ 20:59 |
| `VIX` | 776 | 07:15 ~ 20:15 |
| `SOX` | 391 | **13:30 ~ 20:00** |
| `KOSPI` | 241 | 02:00 ~ 06:00 |

`SOX`의 13:30~20:00 UTC는 09:30~16:00 EDT, 즉 **미국 정규장과 정확히 일치**한다. §1의
표대로 현물 지수가 정규장에만 움직인다는 것이 실제 데이터로 확인됐다. 선물은 거의
연속이다.

### 멱등성

같은 응답으로 두 번 저장했을 때 행 수가 3432에서 그대로였다. `source_record`는 폴링마다
1건씩 늘고 `payload`는 `NULL`이다.

### 조용한 구간 처리 — 실제로 걸렸다

구현을 검증한 시점이 마침 **주말 휴장 구간**이었다(ES=F 마지막 봉이 금요일 16:59 EDT,
CME는 금요일 17:00 ET에 닫는다). 5개 심볼 전부 최근 15분에 0봉이었고 **예외 없이
빈 결과를 돌려줬다.**

원래 설계였던 "0건이면 실패"를 그대로 뒀다면 이 순간 DAG가 5개 심볼 모두 실패했을
것이다. §3.4의 두 단계 판정이 실제로 필요했음이 확인됐다.

### 구현 중 잡은 버그

무한대 값이 들어오면 `YahooPayloadError`가 아니라 pydantic `ValidationError`로 새어
나갔다. DAG는 `YahooPayloadError`만 잡아 해당 심볼을 실패로 넘기므로, 그대로 두면 심볼
하나 때문에 폴링 전체가 죽는다. `parse_bars`에서 봉 생성부를 감싸 변환하도록 고쳤고
`test_parse_rejects_a_non_finite_previous_close`가 이를 고정한다.

### 남은 것

- **`airflow dags test yahoo_quote_intraday`는 아직 안 돌렸다.** Airflow compose 스택과
  `AIRFLOW_CONN_NEWS` 연결이 필요하다. DagBag 로드와 수집기 실동작은 확인했으므로 남은
  것은 Airflow 런타임 배선뿐이다.
- 검증하면서 실제 봉 3432건이 로컬 `quote_bar`에 들어가 있다. 지우려면:

      DELETE FROM quote_bar;
      DELETE FROM source_record WHERE source = 'yahoo';

---

## 8. 과거 구간 백필

### 8.1 Yahoo의 제약 — 실측

| 확인 | 결과 |
| --- | --- |
| `range=1mo` 요청 | `Only 8 days worth of 1m granularity data are allowed to be fetched per request` |
| 2026-06-01~06-07 명시 요청 | `1m data not available` |
| 하루씩 훑어 경계 탐색 | **7/9 이전 없음, 7/10부터 있음** (2026-08-08 기준) |

**Yahoo는 1분봉을 약 30일만 보관하고 요청당 8일까지만 준다.** 그보다 과거는 데이터가 존재
자체를 하지 않는다. 6월 백필은 어떤 방법으로도 불가능하다.

### 8.2 구현

`range=1d`는 "지금 기준 하루치"라 과거 날짜를 가리킬 수 없다. 그래서 `period1`/`period2`로
구간을 직접 준다.

- `modules.collectors.yahoo.backfill_windows(start, end)` — 구간을 8일씩 쪼개는 순수 함수.
  창이 빈틈없이 이어지고 요청 구간을 정확히 덮는지 테스트가 고정한다.
- `fetch_bars(symbol, window)` — `window`가 있으면 `range` 대신 `period1`/`period2`를 쓴다.
- `parse_bars(body, since, until)` — 상한이 **열린 경계**다(`bar_at < until`). 창이 이어질 때
  경계 봉이 두 창에 다 들어가지 않는다.
- `store_bars(connection, responses, since, until, failures)` — `lookback_minutes` 대신 구간을
  직접 받는다. 폴링은 `since`만 주고 백필은 둘 다 준다.
- DAG params `backfill_start` / `backfill_end`(둘 다 포함, YYYY-MM-DD). 주면
  `lookback_minutes`를 무시한다. 창마다 `source_record`가 1건씩 생긴다.

보관 기간을 넘긴 요청은 **태스크 시작 시점에 막는다.** 조용한 0건으로 끝나면 백필이 됐는지
안 됐는지 알 수 없기 때문이다.

    airflow dags trigger yahoo_quote_intraday \
      --conf '{"backfill_start": "2026-07-10", "backfill_end": "2026-07-31"}'

### 8.3 백필한 봉의 `previous_close`는 신뢰할 수 없다

**이건 설계 결함이고 고치지 않았다. 대신 조회 쪽에서 우회한다.**

Yahoo 응답 하나에 `meta.chartPreviousClose`는 **한 값뿐**이고, 그 값은 요청 구간 시작
직전의 종가다. 8일치를 한 번에 받으면 그 한 값이 8일 전체 봉에 박힌다.

실제로 저장된 값을 보면 7/13~7/16이 전부 같다.

| 날짜 | 저장된 `previous_close` | 실제 직전 거래일 종가 |
| --- | --- | --- |
| 2026-07-13 | 7475.94 | 7581.35 |
| 2026-07-14 | 7475.94 | 6904.41 |
| 2026-07-15 | 7475.94 | 6836.90 |
| 2026-07-16 | 7475.94 | 7347.23 |

**실시간 폴링 구간(`range=1d`)에서는 맞다.** 하루치만 받으므로 `chartPreviousClose`가 곧
직전 거래일 종가다. 알림이 도는 경로는 이쪽이라 알림 정확도에는 영향이 없다.

하루 단위로 쪼개 받으면 해결될 것 같지만 **안 된다.** 좁은 구간을 `period1`/`period2`로
요청하면 Yahoo가 요청 구간을 무시하고 최신 세션까지 섞어 돌려준다(실측).

그래서 **대시보드는 저장된 `previous_close`를 쓰지 않고 봉에서 직접 계산한다**(§9).
컬럼 자체는 실시간 경로에서 유효하므로 그대로 둔다.

### 8.4 백필 결과와 데이터 품질

2026-07-10~07-31을 넣었다. 창 3개, **67,531봉**, `state=success`.

봉 수가 구조적으로 맞는지 확인했다.

| 심볼 | 하루 봉 수 | 검증 |
| --- | --- | --- |
| `SOX` | 390 | 미국 정규장 6.5시간 × 60 과 정확히 일치 |
| `KOSPI` | 360 | 평일만. 주말 0 |
| `SP500_FUT` | ~1370 | 일요일에 110(선물 개장) |

**저장 경로에는 버그가 없다.** 같은 날짜를 다시 받아 저장값과 대조했더니 봉 수·최저·최고·
첫봉·마지막봉이 전부 일치했다.

다만 **Yahoo의 `^KS11`(코스피) 분봉 품질은 낮다.** 백필 구간에서 일중 변동이 5~10%로
나오는 날이 있는데 지수가 그렇게 움직이지 않는다. 재요청해도 같은 값이 오므로 제공처가
주는 값 자체가 그렇다. 미국 선물·SOX는 백필과 실시간이 비슷한 수준이라 문제가 없다.

**그래서 코스피를 KIS로 옮겼다**(§10.8). 마이그레이션이 마스터 행의 `provider`를 `kis`로
바꾸고 Yahoo가 쌓아 둔 코스피 봉 5,641개를 지운다. 품질이 낮아 남겨 둘 값어치가 없고,
마스터가 옮겨가면 참조하는 행이 없어 대시보드에서도 사라진다.

---

## 9. Grafana 대시보드

| 파일 | uid | 제목 | 보는 심볼 |
| --- | --- | --- | --- |
| `quote-index.json` | `market-quote-index` | 지수 장중 | `kind = 'index'` |
| `quote-index-future.json` | `market-quote-index-future` | 지수선물 장중 | `kind = 'index_future'` |
| `quote-intraday.json` | `market-intraday-quote` | 지수·선물 통합 장중 | 전부 |

셋은 **패널 구성이 같고 어떤 심볼을 보느냐만 다르다.** 국채 쪽이 미국·국내·통합 셋으로
나뉜 것과 같은 구성이다.

### 9.1 기존 대시보드와 목적이 다르다

처음에는 기존 국채·환율 대시보드의 구성(심볼 반복 stat + 시계열 + 표)을 그대로 따라
만들었다가 다시 만들었다. 기존 넷은 **"지표를 시간축에 그린다"** 가 목적이라 그 틀을 쓰면
"1분봉으로 바꾼 국채 대시보드" 이상이 되지 않았다.

이 대시보드들의 목적은 둘이다.

- **알림 임계값을 근거 있게 정한다.** §1에서 "데이터가 쌓이면 분포를 보고 정한다" 고 해
  놓고 그 분포를 볼 화면이 없으면 결국 숫자를 찍게 된다.
- **한국 장중에 미국 선물이 움직였는지 본다.** 이 수집이 존재하는 이유 자체다.

그래서 중심이 시계열이 아니라 **급변 이벤트 표와 변화율 분포 표**다.

### 9.2 `reference.quote_symbol` 마스터

**대시보드를 셋으로 나누려면 무엇이 현물이고 무엇이 선물인지 조회하는 쪽이 알아야 한다.**
`quote_bar`는 `(provider, symbol)`까지만 안다. 그래서 §3.1에서 미뤄 뒀던 마스터를 만들었다.

`reference.indicator_series`가 국채 대시보드에 하는 역할과 같고 규칙도 같다.

- 자연키는 `(provider, symbol)`. `kind`는 `index` / `index_future`이고 `CHECK` 제약을 건다.
- **`quote_bar`에서 이 테이블로 외래키를 걸지 않는다.** 걸면 마스터 행이 없는 심볼을
  수집기가 저장하지 못해, Enum에만 추가하고 시드를 빠뜨린 순간 DAG가 죽는다. 대신
  `tests/migrations/test_quote_symbol_catalog.py`가 수집기 Enum과 시드를 대조한다.
- 시드는 마이그레이션이 넣는다. 리비전에서 앱 코드를 import하지 않는다.
- **심볼을 늘릴 때는 수집기 Enum과 마스터 시드를 같은 커밋에서 함께 늘린다.**

이 테이블은 **시세를 하나도 담지 않는다.** 5행이고 수집기·DAG는 이 때문에 바뀌지 않았다.

| symbol | kind | country | label |
| --- | --- | --- | --- |
| `SP500_FUT` | `index_future` | US | S&P500 선물 |
| `NASDAQ100_FUT` | `index_future` | US | 나스닥100 선물 |
| `VIX` | `index` | US | VIX 변동성 지수 |
| `SOX` | `index` | US | 필라델피아 반도체 지수 |
| `KOSPI` | `index` | KR | 코스피 |

마스터의 `label` 은 단순한 설명이 아니라 **화면에 그대로 나가는 이름**이다(§9.10).

**대시보드 어디에도 심볼 이름이 적혀 있지 않다.** 심볼 변수가 마스터에서 읽는다.

```sql
SELECT label AS "__text", symbol AS "__value" FROM quote_symbol
WHERE provider = '$provider' AND kind = 'index_future' ORDER BY kind, symbol
```

이게 없으면 세 대시보드 SQL에 심볼 목록을 박아야 하고, 심볼을 하나 추가할 때 JSON 세 개를
고쳐야 한다.

### 9.3 나누니 데이터가 차이를 스스로 보여 준다

"한국 정규장 시간대만" 패널을 각 대시보드에서 열면 이렇게 갈린다(실측).

| 대시보드 | 그 패널에 남는 심볼 |
| --- | --- |
| 지수 | **`KOSPI` 하나.** `SOX`·`VIX`는 그 시간에 봉이 아예 없다 |
| 지수선물 | **`KOSPI200_FUT`·`NASDAQ100_FUT`·`SP500_FUT` 셋 다** |

**선물 대시보드의 이 패널이 이 수집의 목적 그 자체다.** 한국 장중에 나스닥 선물이 빠질 때
코스피200 선물이 어떻게 반응하는지가 한 축에 그려진다. 현물만 보면 그 시간대는 코스피
하나뿐이라 비교 자체가 성립하지 않는다.

§1의 표가 실제 데이터로 나타나는 지점이고, 한 화면에 섞여 있을 때는 보이지 않던 것이다.

임계값 기본값도 갈라 뒀다. 선물은 정상 변동폭이 작아서(S&P500 선물의 15분 상위 1%가 0.3%
수준) **선물 대시보드만 `0.3`**, 나머지는 `1.0`이다. 선물에 1.0을 쓰면 이벤트 표가 네 줄만
나온다.

통합 대시보드의 급변 이벤트 표에는 **"종류" 칸**이 있다. 현물과 선물이 섞이므로 구분이
필요하고, 그 값도 마스터에서 조인해 온다.

### 9.4 패널

| 패널 | 내용 |
| --- | --- |
| 심볼별 stat | 마지막 봉의 직전 거래일 대비 변동률 |
| **급변 이벤트** | `window_min`분 변화가 ±`threshold`% 를 넘은 사건. **연속한 분은 한 사건으로 묶는다** |
| **임계값 참고** | 심볼별 변화율 분위수와, 그 임계값이 조회 구간에서 몇 분 울렸을지 |
| 직전 거래일 대비 변동률 | 심볼 비교. 한국 정규장이 주황 음영으로 깔린다 |
| 최근 N분 변화율 | "방금 빠졌다". 임계값이 점선으로 표시된다 |
| 한국 정규장 시간대만 | 그 구간 봉만 남기고 구간 시작을 0으로 재정규화 |
| 수집 상태 | 심볼별 마지막 봉과 지연 |

변수는 `ds`, `provider`, `symbol`, `window_min`, `threshold`다.

**`provider`는 다중 선택이고 기본이 전체다.** 처음에 단일 선택으로 만들었다가 고쳤다.
단일이면 나스닥100 선물(yahoo)과 코스피200 선물(kis)을 **같은 축에 놓을 수 없는데**, 그게
이 수집의 핵심 비교다. `symbol`이 제공처 안에서만 고유하므로 조회에는 항상 함께 건다.
지금은 두 제공처의 심볼 이름이 겹치지 않아 범례가 `symbol` 하나로 구분된다. 겹치는 이름이
생기면 범례에 provider를 붙여야 한다.

### 9.5 왜 이벤트를 묶는가

묶지 않으면 **사건 하나가 표를 다 먹는다.** 15분 낙폭 상위 8건을 그냥 정렬했더니 전부
7/31 13:44~13:52Z 의 SOX 급락 한 건이었다. 같은 사건의 연속된 분들이다.

10분 넘게 조용하면 다음 사건으로 끊는다(gaps-and-islands).

```sql
sum(CASE WHEN prev_at IS NULL OR bar_at - prev_at > INTERVAL '10 minutes' THEN 1 ELSE 0 END)
  OVER (PARTITION BY symbol ORDER BY bar_at) AS grp
```

### 9.6 임계값은 심볼마다 달라야 한다

"임계값 참고" 표가 그걸 바로 보여 준다. 15분 ±1% 기준으로 조회 구간에서 울린 분 수:

| 심볼 | 종류 | 상위 1% | 최대 낙폭 | -1% 이하(분) |
| --- | --- | --- | --- | --- |
| `SP500_FUT` | 선물 | 0.31% | -0.63% | **0** |
| `NASDAQ100_FUT` | 선물 | 0.57% | -1.26% | **5** |
| `SOX` | 지수 | 1.84% | -3.78% | 271 |
| `KOSPI` | 지수 | 2.89% | -4.18% | 558 |
| `VIX` | 지수 | 3.63% | -7.04% | **1288** |

같은 -1% 인데 SP500 선물은 한 번도 안 울리고 VIX 는 1288분 울린다. **임계값 하나를 전부에
쓰면 안 된다**는 게 숫자로 나온다. 종류별로 대시보드를 나눈 이유이기도 하다.

VIX 는 변동성 지수라 정상 변동폭이 다른 지수의 열 배쯤 된다. 같은 축에서 비교하면 나머지가
납작해지므로 필요할 때만 심볼 목록에서 켠다.

### 9.7 기준값을 저장 컬럼에서 안 읽는 이유

§8.3 때문이다. 패널은 `quote_bar` 에서 **직전 거래일(UTC)의 마지막 종가**를 직접 계산한다.

```sql
WITH bars AS (
  SELECT bar_at, symbol, close FROM quote_bar
  WHERE provider = '$provider' AND symbol IN (${symbol:sqlstring})
    -- 기준일을 잡으려면 조회 구간보다 앞을 더 읽어야 한다
    AND bar_at >= $__timeFrom()::timestamptz - INTERVAL '7 days'
    AND bar_at <= $__timeTo()::timestamptz
), daily AS (
  SELECT symbol, bar_at::date AS d,
         (array_agg(close ORDER BY bar_at DESC))[1] AS day_close
  FROM bars GROUP BY symbol, bar_at::date
), baseline AS (
  SELECT symbol, d, lag(day_close) OVER (PARTITION BY symbol ORDER BY d) AS prev_close
  FROM daily
)
```

알아둘 것 둘:

- **UTC 날짜 경계는 근사다.** 선물은 거의 24시간 돌아서 실제 세션 경계와 정확히 같지 않다.
  비교 기준을 하나로 고정하려는 선택이다.
- **수집 공백이 있으면 기준일이 그만큼 멀어진다.** 지금 데이터는 7/31과 8/7 사이가 비어
  있어서 8/7의 기준이 일주일 전이고, 그래서 SP500 선물이 +3.4%로 보인다. 틀린 계산이
  아니라 그 구간에 데이터가 없어서다. 연속 수집이 돌면 해소된다.

### 9.8 "최근 N분 변화율"의 구간

행 개수가 아니라 **시간 기준**이다.

```sql
first_value(close) OVER (
  PARTITION BY symbol ORDER BY bar_at
  RANGE BETWEEN INTERVAL '${window_min} minutes' PRECEDING AND CURRENT ROW
)
```

`lag(N)` 을 쓰면 거래가 없던 분이 건너뛰어져 있어 N행 전이 N분 전이 아니다. 조회 구간 맨 앞
`window_min` 분은 구간이 잘려 값이 0에 가깝게 나온다.

### 9.9 한국 정규장 음영

대시보드 annotation 으로 넣는다. 날짜 목록을 하드코딩하지 않고 조회 구간에서 만든다.

```sql
SELECT
  (g.d::date + TIME '09:00') AT TIME ZONE 'Asia/Seoul' AS time,
  (g.d::date + TIME '15:30') AT TIME ZONE 'Asia/Seoul' AS "timeEnd",
  '한국 정규장' AS text
FROM generate_series($__timeFrom()::date, $__timeTo()::date, INTERVAL '1 day') AS g(d)
WHERE EXTRACT(DOW FROM g.d) BETWEEN 1 AND 5
```

공휴일은 걸러내지 않는다. 그날은 봉이 없어서 음영만 깔리고 선이 안 그려지므로 오해가
생기지 않는다. 공휴일 캘린더를 들이는 값어치가 없다.

### 9.10 표시 이름 — 화면에는 한국어를 먼저 둔다

**원시 심볼을 화면에 그대로 쓰지 않는다.** `COPPER` 보다 `구리(COPPER)` 가 읽힌다.
심볼 드롭다운, stat 패널 제목, 시계열 범례, 표의 값까지 전부 이 형식이다. 표의 컬럼명도
`심볼` 이 아니라 `종목` 이다.

**괄호에 심볼을 남기는 이유**가 있다. 이름만 두면 화면에서 본 것과 SQL·알림 설정에서 쓰는
값의 연결이 끊긴다. "구리가 빠졌네" 다음에 "쿼리에 뭘 넣지?" 가 된다. `구리(COPPER)` 면
둘 다 보인다.

이름을 SQL에 박지 않는다. **마스터에서 조인해 온다.**

```sql
JOIN quote_symbol m ON m.provider = q.provider AND m.symbol = q.symbol
-- m.label || '(' || m.symbol || ')' AS display
```

그래서 **이름을 고치려면 `quote_symbol` 한 행만 바꾸면 된다.** 대시보드 다섯 개를 건드리지
않아도 다섯 화면에 동시에 반영된다. 심볼을 추가할 때도 시드의 `label` 이 그대로 화면
이름이 되므로, **`label` 을 적을 때는 그게 사람이 읽을 이름이라는 것을 염두에 둔다.**

집계 기준도 `symbol` 이 아니라 `display` 로 묶는다. 마스터의 자연키가 `(provider, symbol)`
이라 1:1이므로 그룹이 쪼개지지 않는다.

### 9.11 넣지 않은 것

- **거래량 패널.** `SOX` 와 `VIX` 는 거래량이 전부 0으로 온다(현물 지수라 없다). 절반이 빈
  패널을 두는 대신 뺐다.
- **선행 관계 통계.** "NQ 가 빠지면 몇 분 뒤 KOSPI 가 빠진다" 를 상관계수로 내는 패널은
  만들지 않았다. 데이터가 몇 주치뿐이고 Yahoo 의 `^KS11` 분봉 품질도 낮아서(§8.4) 그럴듯한
  숫자가 나와도 근거가 없다. 대신 "한국 정규장 시간대만" 패널로 눈으로 비교하고, 그게
  눈으로 보는 비교라는 걸 패널 설명에 적어 두었다.
- **국가별 분리.** 마스터에 `country` 를 넣어 뒀지만 지금은 대시보드가 쓰지 않는다. 수집
  대상이 미국 넷·한국 하나라 나눌 값어치가 없다. 나라가 늘면 `kind` 처럼 변수로 만든다.

---

## 10. 국내 수집 (KIS)

`modules/collectors/kis.py` · `dags/kis_quote_intraday.py`

### 10.1 왜 KIS인가

**Yahoo에 KOSPI200 선물이 없다.** 실측으로 `KS200.KS`·`101RC000.KS` 둘 다 Not Found다.
현물 지수(`^KS200`, `^KQ11`)는 있는데 KRX 파생은 취급하지 않는다.

KIS는 준다. 그리고 **국내 시세는 무료다** — 미국 선물을 막았던 `EGW00550`(CME SUB거래소
시세료)에 해당하지 않는다. 프로브에서 `rt_cd: 0`으로 바로 조회됐다.

이 값이 중요한 이유는 §1의 표에 있다. 한국 정규장 시간에 살아 있는 신호가 미국 선물과
코스피200 선물 둘뿐이고, 후자가 **한국 쪽 반응을 보는 가장 직접적인 값**이기 때문이다.

### 10.2 종목코드 — 마스터 파일로 확정했다

프로브로 코드를 찾다가 두 번 헛다리를 짚었다. 기록해 둔다.

| 시도 | 결과 |
| --- | --- |
| 전광판 `FID_COND_MRKT_CLS_CODE=MKI` | **미니** KOSPI200 선물(`A056xx`, "미니F") |
| `KQI` | **코스닥150** 선물(`A066xx`) |
| `KI`·`K2`·`KSI`·`KSP`·`MNI`·`F`·`MF`·`SPI` | 전부 빈 응답 |
| `A101xx`·`101T12` 형식 | 존재하지 않음 |
| `A0nn2609` 00~99 전수 조사 | **아무것도 못 찾음** (유량 제한으로 보임) |

전수 조사가 실패한 건 유량 제한 탓도 있지만 **형식 자체가 틀렸기 때문**이다. 답은 KIS
종목 마스터 파일에 있었다.

    https://new.real.download.dws.co.kr/common/master/fo_idx_code_mts.mst.zip

```
1|A01609|KR4A01690002|F 202609| |00000.00|1|2001|KOSPI200
1|A05608|KR4A05680006|미니F 202608| |00000.00|1|2001|KOSPI200
1|A06609|KR4A06690009|코스닥150F 202609| |00000.00|1|2001|KSQ150
```

**코드는 `A0` + 상품 + 연도 끝자리 + 만기월이다.** 상품번호가 아니라 **연도**가 세 번째
자리에 온다. 그래서 `A0nn` 형식으로 훑는 접근이 애초에 성립하지 않았다.

| 상품 자릿수 | 상품 |
| --- | --- |
| **`1`** | **KOSPI200 정규 선물** (`A01609` = "F 202609") |
| `5` | 미니 KOSPI200 선물 |
| `6` | 코스닥150 선물 |

**미니가 아니라 정규를 쓴다.** 계약 크기가 1/5라 다른 상품이고 거래량도 다르다.

정규 계약 안에서도 최근월물이 거의 전부다(실측, 같은 102봉 구간):

| 코드 | 만기 | 구간 거래량 |
| --- | --- | --- |
| **`A01609`** | 2026-09 | **16,393** |
| `A01612` | 2026-12 | 119 |
| `A01703` | 2027-03 | 142 |

### 10.3 월물 롤오버

코드를 하드코딩하지 않는다. `front_contract(future, today)`가 날짜에서 계산한다.

- 분기물(3·6·9·12)이다. 미니와 달리 월물이 없다.
- 만기는 만기월 **두 번째 목요일**이다. 만기 당일은 아직 거래되므로 포함한다.

검증(테스트로 고정):

| 날짜 | 계약 |
| --- | --- |
| 2026-08-08 | `A01609` |
| 2026-09-10 (만기 당일) | `A01609` |
| 2026-09-11 | **`A01612`** |
| 2026-12-11 | `A01703` (연도 자릿수도 넘어간다) |

규칙이 틀리면 조회가 0봉으로 끝나는데, `parse_bars`가 빈 응답을 실패로 만들기 때문에 조용히
넘어가지 않는다.

실제 월물은 `quote_bar.contract_code`에 저장한다. 월물이 바뀌면 가격에 갭이 생기는데 이
값이 없으면 갭이 시장 급변인지 롤오버인지 구분할 수 없다. Yahoo는 연속 심볼(`ES=F`)을
주므로 `NULL`이다.

### 10.4 Yahoo 수집기와 다른 점

| | Yahoo | KIS |
| --- | --- | --- |
| 정렬 | 오름차순 | **최신순** — 저장 전에 뒤집는다 |
| 한 번에 | 하루치 통째 (600봉+) | **102봉** |
| 시각 | epoch | **KST 벽시계** `YYYYMMDD`+`HHMMSS` |
| 값 | 숫자 | **공백 패딩 문자열** `"      976.16"` |
| 심볼 | 연속(`ES=F`) | 월물 지정 |
| 전일종가 | `chartPreviousClose` (백필 시 부정확, §8.3) | **`futs_prdy_clpr` — 항상 정확** |
| 요청 도구 | scrapling (429 회피) | **stdlib `urlopen`** (WAF 없음) |
| 오류 | HTTP 상태 | **본문 `rt_cd`.** 500에 비표준 JSON을 담기도 한다 |

마지막 줄이 특히 중요하다. KIS는 실패를 500으로 내면서 본문에 `{rt_cd:"1","msg1":"..."}`
처럼 **키에 따옴표가 없는 JSON**을 담는다(실측). 표준 파서로 읽으면 사유를 통째로 잃는다.
그래서 `_extract_message`가 정규식으로 `msg_cd`/`msg1`을 긁는다.

### 10.5 DAG — 24시간 돌지 않는다

`yahoo_quote_intraday`는 미국 선물이 거의 24시간 거래돼서 시간 창 없이 돈다. 여기는 다르다.

```
schedule="*/5 8-16 * * 1-5"   # KST 평일 08:00~16:59 = UTC 평일 23:00~07:59
```

**야간장은 이 API로 오지 않는다.** 야간 시각을 넣어도 정규장 마감(15:45 KST)으로 잘리고
`krx-ngt-*` REST 엔드포인트는 **404**다(실측). 웹소켓만 되는 것으로 보이며 상주 프로세스가
필요해 이번 범위 밖이다. 그래서 밤새 빈 호출을 하지 않는다.

야간선물이 REST로 열리면 미국 장 시간대를 한국 선물로 덮을 수 있어 값어치가 크다. 그때
다시 본다.

**토큰은 Airflow Variable에 캐시한다.** 발급 횟수 제한이 있어 폴링마다 받을 수 없다
(실제로 연속 발급하다 403을 받았다). 24시간짜리이고 만료 30분 전에 갈아 끼운다. 401을
만나면 한 번만 재발급하고 다시 시도한다.

`lookback_minutes` 상한은 **102**다. 한 번에 그만큼만 오므로 더 넓게 잡으면 조용히 구멍이
생긴다. Yahoo(1440)와 다르다.

### 10.6 검증

| 검사 | 결과 |
| --- | --- |
| `pytest tests` | **252 passed** (KIS 29개 포함) |
| `ruff` · `pyrefly` | 통과 · 0 errors |
| DagBag | `import 오류: 없음`, 5개 DAG |
| `airflow dags test kis_quote_intraday` | **success**. 토큰 발급·캐시, 월물 `A01609` 해석 |
| 실제 저장 | 102봉. 재실행해도 102행(멱등) |
| 정렬 | 최신순 → 오름차순 변환 확인 |
| 마지막 봉 | `06:45Z` = **15:45 KST** 정규장 마감 |

DAG 실행 시점이 토요일이라 `Stored 0 bars ... KOSPI200_FUT=A01609`로 끝났다. **장 밖에서
0봉인 것은 성공이다.** Yahoo 쪽과 같은 규칙이다(§3.4).

### 10.7 남은 것

- **야간선물.** REST로 안 되므로 웹소켓 상주 프로세스가 필요하다. 되면 미국 장 시간대를
  한국 선물로 덮을 수 있다.
- **코스닥150 선물**(`A066xx`)과 **미니**(`A056xx`). 코드 체계를 알아냈으니 필요하면
  `DomesticFuture`에 한 줄과 마스터 시드 한 줄이면 된다.
- **국내 주식 개별 종목.** 붙일 때 **KRX 기준만 쓴다** — 조회의
  `FID_COND_MRKT_DIV_CODE`는 `J`(KRX)이고 `NX`(NXT)나 `UN`(통합)은 쓰지 않는다.
  통합 시세는 두 거래소 체결을 섞어 KRX 단독과 값이 달라진다.

### 10.8 국내 지수 — 코스피·코스피200

업종 분봉 `/uapi/domestic-stock/v1/quotations/inquire-time-indexchartprice`
(`FHKUP03500200`), `FID_COND_MRKT_DIV_CODE='U'`(업종). **KRX 지수다** — NXT는 지수를 내지
않아 이 조회에는 거래소 구분이 없다.

| 업종코드 | 심볼 | |
| --- | --- | --- |
| `0001` | `KOSPI` | Yahoo `^KS11`에서 옮겨 왔다 |
| `2001` | `KOSPI200` | **코스피200 선물의 기초지수** |

**`FID_ETC_CLS_CODE`는 반드시 `1`이다.** 기본값 `0`으로 부르면 시각이 `999999`(장마감)와
`888888`(시간외)인 **의사 봉**이 섞여 들어와 시각 파싱이 깨진다. 실측으로 확인했고 상수에
이유를 적어 두었다.

```
ETC=0 : ['999999', '888888', '153200', ...]   <- 앞 두 개가 의사 봉
ETC=1 : ['153200', '153100', '153000', ...]
```

값 컬럼도 선물과 다르다. 선물은 `futs_*`, 업종지수는 `bstp_nmix_*`이고 전일종가는 각각
`futs_prdy_clpr` / `prdy_nmix`다. `KisRawBar.prices()`가 채워진 쪽을 고른다.

**코스피200을 함께 받는 이유는 베이시스다.** 봉 단위 베이시스를 API가 주지 않아서 현물과
선물을 각각 받아 조회 쪽에서 뺀다. 현물이 없으면 계산 자체가 성립하지 않는다.

| KST | 선물 | 현물 | 베이시스 |
| --- | --- | --- | --- |
| 15:32 | 979.35 | 974.72 | **+4.63** |
| 15:29 | 980.25 | 975.03 | +5.22 |

교차 검증도 했다. 같은 시점 코스피 종가가 KIS 6258.71, Yahoo 6258.77로 사실상 같다.
품질 문제는 값의 수준이 아니라 **일중 변동폭**에 있었다.

---

## 11. 심볼 확장 — 6종에서 25종으로

수집 대상이 **6종 → 13종 → 25종**으로 두 번 늘었다. 그 과정에서 `kind`가 **둘에서 일곱**이
됐다. 이 절은 무엇을 왜 붙였고 `kind`를 왜 그렇게 갈랐는지를 남긴다.

심볼은 전부 붙이기 전에 실제로 호출해 확인했다. **되는지 모르는 걸 넣지 않는다.**
`HSTECH`(항셍테크)는 Yahoo에 없어서 뺐다.

심볼을 추가할 때 마스터 시드의 `label` 이 **화면에 그대로 나가는 이름**이라는 점을
염두에 둔다(§9.10). 대시보드는 `구리(COPPER)` 처럼 `label(symbol)` 형식으로 보여 준다.

### 11.1 무엇을 왜 붙였는가

| 심볼 | 종류 | 소스 | 왜 |
| --- | --- | --- | --- |
| `KOSPI200` | `index` | KIS `2001` | **베이시스의 기준값.** 선물은 받는데 현물이 없어 괴리를 계산할 수 없었다 |
| `US10Y` | `rate` | `^TNX` | **장중 금리가 전혀 안 보였다.** FRED는 하루 한 값이다 |
| `USDKRW` `USDJPY` `DXY` | `fx` | `KRW=X` `JPY=X` `DX-Y.NYB` | **거의 24시간 움직여 한국 정규장을 채운다** |
| `NIKKEI225` `TAIEX` | `index` | `^N225` `^TWII` | **한국 정규장과 겹치는 해외 현물** |
| `HSI` `SSE_COMP` | `index` | `^HSI` `000001.SS` | 중화권 심리. 역시 한국 장중과 겹친다 |
| `RUSSELL2000` | `index` | `^RUT` | 나스닥 대비 상대강도로 시장 폭(breadth)을 본다 |
| `USDCNH` `JPYKRW` | `fx` | `CNH=X` `JPYKRW=X` | 역외 위안이 시장 스트레스를 본토보다 빨리 반영한다 |
| `US10Y_FUT` | **`bond_future`** | `ZN=F` | **아시아 세션에 살아 있는 유일한 미 금리 신호**(§11.3) |
| `GOLD` `SILVER` `COPPER` `WTI` | **`commodity`** | `GC=F` `SI=F` `HG=F` `CL=F` | 금은 위험회피, 은·구리는 경기·산업수요, 유가는 인플레 |
| `TSMC_ADR` | **`equity`** | `TSM` | 반도체 공급망 참조가. 시그널 대상이 아니라 맥락용 |
| `KOSDAQ` | `index` | KIS `1001` | 코스피와 같은 업종 분봉 경로라 추가 비용이 없다 |

### 11.2 `kind`가 일곱이 된 이유

`index` / `index_future` 둘로는 부족해졌다. **정상 변동폭의 자릿수가 다르거나 읽는 단위가
다른 값을 한 축에 겹치면 읽을 수 없기 때문이다.**

| kind | 왜 따로인가 |
| --- | --- |
| `fx` | 환율은 지수보다 변동폭이 훨씬 작다. 임계값 기본값이 `0.2`인 이유다 |
| `rate` | 금리는 **변화율(%)이 아니라 bp로 읽는다.** 4.66→4.70은 "+0.86%"가 아니라 "+4bp"다 |
| `bond_future` | **수익률이 아니라 가격이다**(§11.3) |
| `commodity` | 넷이 각자 읽는 방향이 다르고 지수와 변동폭도 다르다 |
| `equity` | 개별 종목. 지수와 성격이 다르다 |

CHECK 제약은 값을 늘릴 때마다 다시 만든다. PostgreSQL native enum을 쓰지 않는 대신 치르는
비용이고, 대신 값 추가가 트랜잭션 안에서 끝난다.

```sql
kind IN ('index', 'index_future', 'fx', 'rate', 'bond_future', 'commodity', 'equity')
```

환율의 `country`는 USD가 아닌 쪽의 국가로 둔다(`USDKRW` → KR). 달러인덱스는 달러 자체를
재는 지수라 US다.

### 11.3 `rate`와 `bond_future`를 나눈 이유

**이게 이번 확장에서 가장 틀리기 쉬운 지점이다.**

| | `US10Y` | `US10Y_FUT` |
| --- | --- | --- |
| Yahoo | `^TNX` | `ZN=F` |
| 값 | **수익률** 4.66(%) | **가격** 110(달러) |
| 방향 | 금리 상승 = 값 상승 | 금리 상승 = 값 **하락** |
| 확인된 봉 수 | 400 | **946** |

둘 다 "미 10년물"인데 **서로 반대로 움직인다.** 한 패널에 겹치면 읽는 사람이 반드시 틀린다.
그래서 `kind`로 갈라 다른 화면에 두고, 통합 대시보드 설명에도 같이 켜지 말라고 적었다.

**봉 수 차이가 `US10Y_FUT`를 넣은 진짜 이유다.** `^TNX`는 미국 정규장에만 움직여 400봉이고
`ZN=F`는 946봉이다. 즉 **한국 장중에 살아 있는 미 금리 신호는 국채선물뿐이다.** 수익률만
받으면 이 수집의 목적인 "한국 장중 신호"에서 금리가 통째로 빠진다.

### 11.4 대시보드 — 다섯으로 늘었다

| 대시보드 | uid | 필터 | 임계값 기본 |
| --- | --- | --- | --- |
| 지수 장중 | `market-quote-index` | `kind = 'index'` | 1.0% |
| 지수선물 장중 | `market-quote-index-future` | `kind = 'index_future'` | 0.3% |
| 환율 장중 | `market-quote-fx` | `kind = 'fx'` | 0.2% |
| **원자재 장중** | `market-quote-commodity` | `kind = 'commodity'` | 0.5% |
| 지수·선물 통합 장중 | `market-intraday-quote` | 전부 | 1.0% |

임계값 기본값이 화면마다 다른 이유는 정상 변동폭이 다르기 때문이다. 선물에 1.0%를 쓰면
급변 이벤트 표가 네 줄만 나오고, 환율에 1.0%를 쓰면 통째로 빈다.

**`rate`·`bond_future`·`equity`는 전용 대시보드를 만들지 않았다.** 각각 심볼이 하나뿐이라
화면 하나를 채울 값어치가 없다. 통합 대시보드에만 나온다. 국고채 등이 늘어나면 그때 만든다.

**심볼을 늘려도 대시보드는 안 고친다.** 2순위 12종을 붙이면서 대시보드 JSON은 원자재 하나를
새로 만든 것 말고 손대지 않았다. 심볼 목록을 `quote_symbol` 마스터에서 읽기 때문이다(§9.2).

### 11.5 실측

25종 전부 한 번에 수집해 확인했다. **실패 0건.**

```
Yahoo(21): SP500_FUT=1011  NASDAQ100_FUT=1011  VIX=776   SOX=391
           NIKKEI225=333   TAIEX=271   HSI=345   SSE_COMP=240   RUSSELL2000=391
           US10Y=400       US10Y_FUT=946
           USDKRW=1321  USDJPY=1320  DXY=1020  USDCNH=1320  JPYKRW=270
           GOLD=1020  SILVER=1007  COPPER=1000  WTI=1020  TSMC_ADR=391
KIS  (4):  KOSPI200_FUT=102  KOSPI=102  KOSPI200=102  KOSDAQ=102
```

1,000봉대는 거의 24시간 움직인다는 증거다(환율·원자재·국채선물). 300~400봉대는 각자의
정규장만 움직인다. KIS가 102봉인 것은 한 번에 그만큼만 주기 때문이고 폴링이 이어서 채운다.

대시보드 패널 **35/35** 정상. 테스트 **296개** 통과. `ruff`·`pyrefly` 통과.
DB에 25종 78,345봉.

### 11.6 이번에 잡은 자기 실수

`test_currencies_are_seeded_as_fx`를 처음에 이렇게 썼다.

```python
assert f"'{symbol}', 'fx'" in sql or f'"{symbol}", "fx"' in sql or f"{symbol}" in sql
```

마지막 절 때문에 **무엇을 넣어도 통과하는 테스트**였다. 시드가 `bindparams`를 쓰면 오프라인
SQL에 리터럴이 안 찍힐까 봐 미리 헤지한 것인데, 확인해 보니 그대로 찍혔다. 헤지할 이유가
없었고, 조건을 하나로 조였다.

```python
assert f"'{symbol}', 'fx'" in sql
```

확인하지 않은 걱정으로 테스트를 느슨하게 만들면 그 테스트는 없느니만 못하다.

---

## 12. 다음에 붙일 것 (2026-08-09)

§11이 6종 → 25종 확장의 근거를 남겼다면 여기는 **그 다음**이다. 아직 구현하지 않았고,
후보는 전부 실제 API를 불러 확인했다. 나중에 구현할 때 다시 프로브하지 않아도 되게
응답 필드까지 그대로 적는다.

### 12.1 지금 무엇이 비어 있는가 — 실측

수집 중인 25종을 조회해 보니 목적("한국 장중에 미국 신호를 받는다")에 구멍이 셋 있다.

**구멍 1. 다섯은 한국 정규장에 0봉이다.**

```sql
SELECT s.label, count(*) FILTER (
         WHERE (b.bar_at AT TIME ZONE 'Asia/Seoul')::time BETWEEN '09:00' AND '15:30'
           AND extract(isodow FROM b.bar_at AT TIME ZONE 'Asia/Seoul') <= 5) AS 한국장중,
       count(*) AS 전체
FROM quote_bar b JOIN quote_symbol s USING (provider, symbol) GROUP BY 1 ORDER BY 2;
```

| 심볼 | 한국장중 | 전체 |
| --- | ---: | ---: |
| 러셀2000(RUSSELL2000) | **0** | 391 |
| TSMC ADR(TSMC_ADR) | **0** | 391 |
| 미국 10년물 금리(US10Y) | **0** | 400 |
| 필라델피아 반도체(SOX) | **0** | 6,628 |
| VIX 변동성 지수(VIX) | **0** | 13,172 |
| … | | |
| 나스닥100 선물(NASDAQ100_FUT) | 6,482 | 23,001 |
| S&P500 선물(SP500_FUT) | 6,483 | 22,999 |

이 다섯은 기록용이지 알림 신호가 아니다. `SOX`와 `US10Y`는 §11.3이 이유를 적어 두고
각각 `NASDAQ100_FUT`·`US10Y_FUT`라는 24시간 짝을 붙였다. **러셀만 짝이 없다.** 같은
규칙을 한 심볼에만 안 쓴 것이라 일관성 구멍이다.

**구멍 2. 주말 48시간이 통째로 빈다.**

최근 9일을 KST 시간대별로 세면 토 07:00 이후 봉이 하나도 없고 월 07:00까지 그대로다.

| 심볼 | 주말 구간(토 07:00 ~ 일) |
| --- | ---: |
| ES=F | 359봉 (토 오전에 끊긴다) |
| BTC-USD | **1,628봉** |
| ETH-USD | **1,628봉** |

주말에 지정학·정책 뉴스가 나오면 월요일 개장 전까지 시장 반응을 읽을 값이 없다.

**구멍 3. 한국 반도체 데이터가 없다.**

이 프로젝트의 출발 질문은 "미국 반도체 선물이 빠지면 한국 반도체도 빠지는가"였다.
그런데 **예측 대상이 수집되지 않는다.** `KOSPI`·`KOSDAQ`은 지수라 반도체를 못 본다.
지금 구조로는 알림이 맞았는지 측정할 방법이 없다.

### 12.2 프로브 결과

**KIS.** `A0` + 상품 자릿수 + 연도 끝자리 + 만기월 전 조합을 다시 훑었다(2026-08-07 기준).

| 종목코드 | 이름 | 봉 수 | 판단 |
| --- | --- | ---: | --- |
| `A01609` | F 202609 (KOSPI200) | 102 | 수집 중 |
| `A05609` | 미니F 202609 | 102 | 안 쓴다(§10.2) |
| `A06609` | **코스닥150F 202609** | **102** | **붙일 것** |
| `A08609` | KRX300F 202609 | 102 | KOSPI200과 겹친다 |
| `A04609` | 변동성F 202609 | **5** | 유동성 없음. 제외 |

국채 3년·10년 선물은 `A0[0-9]6xx` 전 자릿수에 없다. 다른 시장 구분 코드가 필요하다.

**Yahoo.**

| 심볼 | 이름 | 봉 수(1d) | 판단 |
| --- | --- | ---: | --- |
| `RTY=F` | E-mini Russell 2000 | 1,430 | 붙일 것 |
| `YM=F` | Mini Dow Jones $5 | 1,439 | 붙일 것 |
| `BTC-USD` / `ETH-USD` | Bitcoin / Ethereum | 67 / 67 | 붙일 것 (아래 주의) |
| `ZT=F` / `ZF=F` | 2년·5년 국채선물 | 1,438 / 1,439 | 3순위 |
| `NG=F` | Natural Gas | 1,439 | 3순위 |
| `000300.SS` | CSI 300 | 331 | SSE_COMP와 겹친다 |
| `^HSCE` | 항셍중국기업 | 400 | HSI와 겹친다 |
| `^KQ11` | Kosdaq Composite | 361 | **국내 우선 원칙 위반.** KIS에 있다 |
| `^SSEC` | — | — | `Not Found` |

`YM=F`의 `E-mini Dow $5`는 **표준 계약이다.** 초소형은 `MYM`이라 §10.2의 "미니가 아니라
정규를 쓴다"에 어긋나지 않는다. 다우는 풀사이즈가 상장폐지돼 이게 정규다.

암호화폐는 `range=1d`가 67봉만 준다. `range=5d`로 받으면 5,828봉이 연속이라 데이터가
없는 게 아니라 `1d`의 구간 정의가 다른 것이다. 폴링은 `lookback_minutes=15`라 지장 없다.
백필할 때만 이 차이를 확인한다.

### 12.3 KIS 주식 분봉 — 세 번째 필드 이름 체계다

`FHKST03010200` / `/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice`.
삼성전자(`005930`)와 SK하이닉스(`000660`) 둘 다 `rt_cd=0`으로 왔다.

```json
output1: {"stck_prdy_clpr": "230500", "hts_kor_isnm": "삼성전자", "stck_prpr": "231000"}
output2: [{"stck_bsop_date": "20260807", "stck_cntg_hour": "130000",
           "stck_oprc": "231000", "stck_hgpr": "231250",
           "stck_lwpr": "230500", "stck_prpr": "230750", "cntg_vol": "85585"}]
```

**구현할 때 가장 먼저 걸리는 지점이 여기다.** `KisRawBar.prices()`는 지금 선물(`futs_*`)과
업종지수(`bstp_nmix_*`) 둘만 안다. 주식은 `stck_*`라 세 번째 분기가 필요하다. 전일종가도
마찬가지로 `futs_prdy_clpr`·`prdy_nmix`가 아니라 `stck_prdy_clpr`이다.

한 요청에 **30봉**만 온다. 선물 102봉, 업종지수 120봉과 다르다. 5분 폴링에는 넉넉하다.

`cntg_vol`은 **진짜 거래량**이다(삼성전자 85,585). 지수가 0을 실어 보내는 것과 다르다.
`quote_bar.volume` 주석이 적어 둔 "0은 거래 없음과 미제공을 구분하지 않는다"의 예외가
처음 생기는 셈이다.

`FID_COND_MRKT_DIV_CODE`는 `J`(KRX)다. `NX`(NXT)와 `UN`(통합)은 쓰지 않는다.
`DomesticIndex` docstring이 적어 둔 규칙 그대로다.

### 12.4 권고 순위

**1순위 — Enum 한 줄 + 마스터 시드 한 줄.**

| 심볼 | 좌표 | 왜 |
| --- | --- | --- |
| `KOSDAQ150_FUT` | KIS 상품 자릿수 `6` | 코스피는 현물+선물 짝인데 코스닥은 현물뿐이다 |
| `RUSSELL2000_FUT` | `RTY=F` | 기존 러셀이 한국 장중 0봉. 다른 심볼에 쓴 규칙을 여기만 안 썼다 |
| `BTC` / `ETH` | `BTC-USD` `ETH-USD` | 주말 48시간을 채우는 유일한 값 |
| `DOW_FUT` | `YM=F` | 가치·경기민감 축. ES·NQ와 갈리는 날이 있다 |

암호화폐는 `kind`가 없다. `crypto`를 여덟 번째로 넣어야 하고 `ck_quote_symbol_kind`
제약 마이그레이션과 대시보드 하나가 따라온다. 나머지 셋은 기존 `kind`로 끝난다.

**2순위 — 국내 반도체 개별주(삼성전자·SK하이닉스).**

가설의 정답지다. `kind`는 `equity`를 그대로 쓴다(TSMC ADR이 이미 쓴다). `_get`이 범용이라
필요한 건 넷이다 — 경로·TR ID 상수, `DomesticEquity` Enum, `KisRawBar`의 `stck_*` 분기,
`fetch_equity_bars`. `fetch_index_bars`와 같은 모양이다.

**3순위** — `ZT=F`·`ZF=F`(장단기 금리차를 한국 장중에 본다. 지금은 10년만 있다), `NG=F`.

**권하지 않음** — `A04609`(5봉), `A08609`(중복), `^KQ11`(국내 우선 원칙), `EURUSD=X`(DXY의
58%가 유로), `000300.SS`·`^HSCE`(중복).

### 12.5 국내 백필이 가능하다

국내 시계열은 **102봉**뿐이다(2026-08-08 시작). Yahoo는 23,001봉이다. 이 상태로는
리드-래그 상관을 볼 수 없다. §10.7이 "KIS는 백필이 없다"고 적었는데 정확하지 않았다.

| 대상 | 과거 조회 | 확인 |
| --- | --- | --- |
| 선물 | **된다** | `FID_INPUT_DATE_1=20260807`, `FID_INPUT_HOUR_1=150000` → 102봉 |
| 주식 | **된다** | 별도 TR `FHKST03010230` (`inquire-time-dailychartprice`) → 2026-08-06 120봉 |
| 업종지수 | **안 된다** | 이 엔드포인트의 `FID_INPUT_HOUR_1`은 기준 시각이 아니라 봉 간격이다(§10.4) |

즉 지수만 앞으로 쌓이는 것을 기다려야 하고 선물과 주식은 과거를 채울 수 있다.
`kis_quote_intraday`가 지금 그 인자를 쓰지 않을 뿐이다.

### 12.6 여기 없는 것

수급·공매도 잔고·경제지표 캘린더·KRX 야간선물·항셍테크는 `quote_bar`에 안 들어간다.
그레인과 계보 규칙이 달라 심볼 한 줄로 끝나지 않는다. 이유는 `docs/collection-map.html`의
"남은 것" 표에 있다.

Yahoo 요청량도 그대로 둔다. 1순위를 다 붙이면 21 → 25종, 하루 6,048 → 7,200회다.
백오프가 없다는 건 코드리뷰가 이미 지적했다.
