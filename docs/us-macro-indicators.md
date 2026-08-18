# 미국 거시지표(CPI·PPI·소매판매) 수집 설계

- 날짜: 2026-08-16
- 상태: 초안
- 범위: FRED에서 미국 월간 거시지표 세 계열을 `indicator_observation`에 편입

## 1. 왜 필요한가

지금 `indicator_observation`에는 **금리만** 쌓인다. 6개국 국채 곡선과 CD 91일, 41계열이 전부
연이율 퍼센트다. 연준이 왜 움직이는지, 왜 달러가 강해지는지를 설명하는 값 — 물가와 소비 — 이
없다.

빈자리가 실제로 드러났다. 문서 태깅 뒤 리포트를 시험 삼아 돌렸을 때 `news` 분석가가 BLS
보도자료를 읽어 *"CPI +0.1%, 실업률 4.1%, 고용 -23000명"* 을 리포트에 넣었다. **그건 기사
본문에서 읽은 문장이지 우리가 가진 시계열이 아니다.** 값으로 갖고 있지 않으니 변화폭을
계산할 수도, 금리·환율과 같은 축에 놓을 수도 없다.

## 2. 미니 나스닥은 범위에서 뺀다

**이미 수집하고 있다.**

```python
NASDAQ100_FUT = ("NASDAQ100_FUT", "NQ=F", "나스닥100 선물")   # yahoo.py
```

`NQ=F`가 곧 E-mini 나스닥100이다. `docs/collection-map.html`에도 "E-mini 나스닥100 연속 선물"로
적혀 있다. 지금 1분봉과 일봉을 둘 다 받고 있고 일봉은 2016년부터 2,515행이 있다.

마이크로(`MNQ=F`)는 계약 크기만 E-mini의 1/10이고 **가격이 사실상 같다.** 상관 분석에 새 정보를
주지 않는다. 필요해지면 `QuoteSymbol`에 한 줄, `quote_symbol` 시드에 한 줄이면 되므로 지금
미리 넣지 않는다.

국내 상장 미국지수 선물은 다른 이야기다. 국내 소스 우선 원칙에 맞지만 KIS 종목코드 확인과
야간장 처리가 함께 붙는 별도 과제다.

## 3. 받을 것

FRED `series` 엔드포인트로 2026-08-16에 확인한 값이다.

| 저장 `series_id` | FRED id | 단위 | `kind` | 보유 구간 |
| --- | --- | --- | --- | --- |
| `CPI_M` | `CPIAUCSL` | `Index 1982-1984=100` | `price_index` | 1947-01 ~ |
| `PPI_M` | `PPIFIS` | `Index Nov 2009=100` | `price_index` | 2009-11 ~ |
| `RETAIL_SALES_M` | `RSAFS` | `Millions of Dollars` | `activity` | 1992-01 ~ |

전부 미국·월간·계절조정이고 관측일이 그 달 1일로 온다(7월 CPI = `2026-07-01`).

### 결정과 이유

- **`M` 접미사는 월간 표시다.** `ecb_irs.py`가 먼저 쓴 규칙이다(`FR10YM`). 한 테이블에 일별과
  월간이 섞여 있어 표시가 없으면 조회하는 쪽이 주기를 구분할 수 없다.
- **FRED id를 저장 식별자로 쓰지 않는다.** `DGS10`은 사람이 읽으니 그대로 뒀지만 `CPIAUCSL`은
  DB만 보고 무슨 값인지 알 수 없다. 제공처 좌표는 수집기 Enum이 들고 있다가 요청과
  `source_record.metadata`에만 쓴다. `ecos.py`의 `MarketRateSeries`가 항목코드를 다루는 방식과 같다.
- **지수 레벨을 저장한다.** 전년 대비 변화율은 저장하지 않는다. FRED가 `units=pc1`로 변환해
  주지만, 원본을 두면 변화율은 언제든 계산되고 반대는 안 된다.
- **`kind`를 둘로 나눈다.** 지수(300 근처)와 백만 달러(70만 근처)를 한 축에 놓을 수 없다.
  단위가 다르면 화면도 갈라야 한다.
- **PPI는 2009-11부터다.** 다른 둘보다 짧다. 상관을 낼 때 표본이 그만큼 잘린다.

## 4. 무엇이 막고 있나

`indicator_series`는 **금리 전용으로 설계돼 있다.** 세 군데가 막는다.

| 걸림 | 위치 | 내용 |
| --- | --- | --- |
| `kind` CHECK | `apps/models/reference.py` | `('government_bond', 'money_market')` 둘뿐 |
| `maturity_months` | 같은 파일 | `NOT NULL` + CHECK `> 0`. **물가지수에는 만기가 없다** |
| `unit` | `airflow/modules/collectors/fred.py` | `SERIES_UNIT = "Percent"` 모듈 상수 하나 |

`indicator_observation.unit` 컬럼 자체는 `Text NOT NULL`이고 제약이 없다. 지금 전 행이
`"Percent"`인 것은 수집기 일곱 개가 각자 같은 상수를 쓰기 때문이다. 컬럼은 이미 준비돼 있고
수집기만 계열별로 갈라 주면 된다.

**만기를 0으로 채우지 않는다.** 만기 없는 값에 만기를 적는 것은 거짓이고,
`global-treasury.json`의 만기 변수 쿼리가 그걸 "0개월물"로 그린다. `NULL`을 허용하고 CHECK를
`maturity_months IS NULL OR maturity_months > 0`으로 바꾼다.

### 대시보드는 안 깨진다

셋 다 확인했다.

- `global-treasury.json` — 아홉 곳 전부 `kind = 'government_bond'`로 거른다. 새 kind는 저절로 빠진다
- `us-treasury.json` — `series` 변수가 custom(하드코딩 4개)이라 새 계열이 섞이지 않는다
- `euro-area/japan/uk-treasury.json` — `provider`로 거르고 `fred`가 아니다

## 5. 바꿀 것

### 5.1 마스터가 만기 없는 지표를 받게 한다

`apps/models/reference.py`

- `SeriesKind`에 `PRICE_INDEX`, `ACTIVITY` 추가
- `maturity_months`를 `nullable=True`로
- CHECK 둘 교체 — `kind IN (넷)`, `maturity_months IS NULL OR maturity_months > 0`

### 5.2 마이그레이션 한 장

`makemigrations`가 Enum·nullable·주석 변경은 잡지만 **CHECK 제약 교체는 만들지 않는다.**
`drop_constraint` → `create_check_constraint` 순서로 손으로 넣는다. 시드 3행도 같은 리비전에
넣고 `maturity_months`는 `None`이다.

`downgrade`는 **시드를 먼저 지운다.** `maturity_months`가 NULL인 행이 남아 있으면 NOT NULL로
되돌릴 수 없다.

### 5.3 수집기에 계열 레지스트리를 둔다

`airflow/modules/collectors/fred.py`의 `TREASURY_SERIES`는 문자열 tuple이라 계열마다 단위를 달
자리가 없다. `ecos.py`의 `MarketRateSeries`와 같은 모양으로 바꾼다.

```python
class FredSeries(StrEnum):
    fred_id: str
    unit: str
    kind: str
    label: str

    DGS10 = ("DGS10", "DGS10", "Percent", "government_bond", "미국 10년물")
    CPI_M = ("CPI_M", "CPIAUCSL", "Index 1982-1984=100", "price_index", "미국 소비자물가지수")
```

- `TREASURY_SERIES`와 `MACRO_SERIES`를 이 Enum에서 파생시킨다. 기존 DAG의
  `collect.expand(series_id=list(TREASURY_SERIES))`는 그대로 돈다.
- `SERIES_UNIT` 상수를 지우고 저장 시 계열의 `unit`을 쓴다.
- **월간 계열은 관측일이 그 달 1일인지 검증하고 아니면 실패시킨다.** 달 중간 날짜가 섞이면
  같은 달이 두 행이 되고 그 뒤로는 어느 쪽이 진짜인지 알 수 없다. `ecb_irs.py`가 같은 검사를 한다.

### 5.4 새 DAG `fred_macro_daily`

`fred_treasury_daily`를 본으로 삼되 셋이 다르다.

| | treasury | macro |
| --- | --- | --- |
| 스케줄 | `30 7 * * 2-6` | `40 7 * * 2-6` |
| lookback | 7일 | **190일** |
| 매핑 | `TREASURY_SERIES` | `MACRO_SERIES` |

**lookback 190일이 핵심이다.** 7월 CPI는 8월 중순에 나온다. 7일 창이면 아직 발표되지 않은 이번
달만 묻고 매번 0건이 온다. `ecb_convergence_monthly`가 같은 이유로 같은 값을 쓴다.

월간 지표를 매일 도는 이유는 **발표일이 불규칙하기 때문이다.** CPI는 다음 달 중순, 소매판매는
중순 전, PPI는 그 사이이고 달마다 요일도 다르다. 발표 달력을 따로 두는 것보다 매일 한 번씩
묻는 편이 싸다 — 계열당 요청 하나, 하루 세 번이다.

국채 DAG에 합치지 않는 이유는 이 lookback 차이와 이름이다. 국채 DAG에 CPI가 들어가면 그 이름이
거짓이 된다.

### 5.5 `daily_series` 뷰가 물가지수를 금리라고 하지 않게 한다

지금 뷰는 `indicator_observation` 전체를 `'rate'`로 표시한다. CPI가 들어오면 **물가지수가
금리로 보인다.** 조회하는 쪽이 그 표시로 계산 방식을 가르기 때문에(금리는 차분, 가격은 로그
수익률) 지수 변화를 퍼센트로 읽지 못한다.

```sql
SELECT o.provider, o.series_id, o.observation_date, o.value,
       coalesce(s.kind::text, 'rate') AS kind
FROM indicator_observation o
LEFT JOIN indicator_series s ON s.provider = o.provider AND s.series_id = o.series_id
```

- `coalesce`가 필요한 이유는 **관측값에서 마스터로 외래키를 걸지 않기 때문이다.** 마스터 행이
  없는 계열도 뷰에 남아야 한다.
- `::text` 캐스트가 필요한 이유는 `CREATE OR REPLACE VIEW`가 컬럼 타입을 바꾸지 못하기
  때문이다. 마스터의 `kind`가 `VARCHAR(20)`이라 캐스트 없이 두면 기존 뷰의 `text`와 어긋나
  교체가 거부된다.

### 5.6 테스트

- `tests/collectors/test_fred.py` — 계열별 단위가 저장되는지, 월간 계열이 1일이 아니면 거부하는지
- `tests/migrations/test_indicator_series_catalog.py` — **kind CHECK 리터럴이 하드코딩돼 있다.**
  새 값 둘을 넣어야 통과한다. `MACRO_SERIES` 대조도 추가한다
- `tests/models/test_reference_models.py` — **컬럼 주석을 dict 동등성으로 검사한다.**
  `kind`·`maturity_months` 주석을 고치면 여기도 같이 고친다

## 6. 안 하는 것

- **Grafana 대시보드** — 이 값의 소비자는 화면이 아니라 리포트 쪽 계산이다. 세 계열로 화면을
  만들 값어치가 없다. 계열이 늘면 그때 만든다.
- **한국 CPI·PPI** — ECOS는 `STAT_CODE`와 `CYCLE`이 모듈 상수로 일별 표에 고정돼 있고 응답
  파서가 `YYYYMMDD` 8자리를 강제한다. 월간 경로를 새로 여는 일이라 별도 과제다.
- **전년 대비 변화율 저장** — 레벨만 둔다. 변화율은 계산된다.
- **BLS 직접 수집** — FRED가 BLS 값을 중계하고 API 하나로 셋이 다 온다. 발표 시각을 분 단위로
  봐야 할 때 그때 BLS를 붙인다.
- **고용지표(실업률·비농업고용)** — 같은 방식으로 붙일 수 있지만 이번 범위 밖이다. 계열 두 줄과
  시드 두 줄이면 된다.

## 7. 검증

1. **FRED id 확인이 먼저다.** 틀린 id에 FRED는 오류가 아니라 빈 `observations`를 준다.
   ```bash
   curl -s "https://api.stlouisfed.org/fred/series?series_id=CPIAUCSL&api_key=$FRED_API_KEY&file_type=json" \
     | python3 -c "import json,sys; s=json.load(sys.stdin)['seriess'][0]; print(s['title'],'|',s['units'],'|',s['frequency'])"
   ```
2. `uv run pytest tests -q`
3. `upgrade head` 뒤 `revision --autogenerate`로 drift 0 확인
4. 백필 한 번
   ```bash
   docker exec airflow-airflow-scheduler-1 airflow dags trigger fred_macro_daily \
     --conf '{"observation_start": "2005-01-01", "observation_end": "2026-08-16"}'
   ```
5. 관측일이 전부 1일인지, 단위가 계열마다 다른지
   ```sql
   SELECT series_id, unit, count(*), min(observation_date), max(observation_date),
          count(*) FILTER (WHERE extract(day FROM observation_date) <> 1) AS not_first_of_month
   FROM indicator_observation WHERE provider = 'fred' GROUP BY 1, 2 ORDER BY 1;
   ```
   `not_first_of_month`가 0이 아니면 월간 검증이 안 걸린 것이다.
6. **기존 대시보드가 안 변했는지 눈으로 본다.** `global-treasury.json`의 국가·만기 드롭다운에
   새 계열이 나타나면 `kind` 필터가 새는 것이다.
7. `daily_series` 뷰에서 `CPI_M`의 `kind`가 `price_index`로 나오는지. `rate`면 5.5가 빠진 것이다.
