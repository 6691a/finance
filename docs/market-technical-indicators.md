# 국내 기술적 보조지표·매매 신호 기능 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task.

**Goal:** KOSPI·KOSDAQ과 추적 국내 종목의 확정 일봉에서 SMA·RSI·MACD·거래량 비율을 계산해 시장 추론과 Slack 브리핑에 투자 참고용 관측값으로 제공하고, 같은 계산에서 이평선·MACD·RSI 매매 신호(사건)를 검출해 저장·채점한다.

**Architecture:** KIS 지수 일봉만 기존 `index_daily`에 추가 수집한다. 국내 종목은 이미 수집 중인 `stock_investor_trade_daily`의 OHLCV를 재사용한다. 두 원천을 읽기 전용 SQL로 정규화하고 순수 Python 함수가 요청 시 지표를 계산한다. 지표값은 저장하지 않고 기존 `daily_history` 툴과 `MarketSummary`가 함께 사용한다. 매매 신호만은 **사건**이라 `technical_signal` 테이블에 저장하고(12절), 지평별 사후 수익률로 채점해 규칙을 고쳐 나간다 — `thesis`의 `prompt_version`·Brier와 같은 형태다.

**Tech Stack:** Python 3.13, Pydantic, PostgreSQL, Apache Airflow 3.3, matplotlib(기존 분봉 차트만), pytest, ruff, pyrefly

**Spec:** 이 문서의 1~8절·12절·14절이 설계 계약이고, 9절이 구현 순서다. 13절은 2026-08-23 검토에서 고친 점의 기록이다. 14절은 지표와 신호를 LLM 추론의 입력·근거·평가에 쓰는 방법이다.

## Global Constraints

- 기능은 정보 제공과 가설 검토에만 쓴다. **주문 호출, 포지션 크기, 손절·익절, 종합 점수는 만들지 않는다.** 매매 신호는 "골든크로스가 났다" 같은 사건 기록이고 그 자체가 판정이 아니다(12절).
- 새 DB 테이블은 `technical_signal` 하나뿐이다(12.2절). 리비전은 손으로 쓴다 — 운영 DB에 `makemigrations`를 돌리지 않는다. 외부 패키지를 추가하지 않는다.
- pandas·numpy·TA-Lib·mplfinance를 추가하지 않는다. 계산은 표준 라이브러리로 충분하다.
- 지표는 확정 일봉에서 조회 시 계산한다. 원천 OHLCV가 재현 가능한 값이므로 파생값을 중복 저장하지 않는다. 신호는 파생값이 아니라 사건이라 저장한다 — 언제 무엇이 났는지를 나중에 채점해야 하기 때문이다.
- `created_at <= as_of_at`을 지켜 과거 추론이 당시 DB에 없던 봉·신호를 보지 않게 한다.
- 기존 당일 분봉 차트와 그 실패 격리 계약은 바꾸지 않는다.
- 구현은 `feature-technical-signals` worktree에서 한다. 사용자가 별도로 요청하지 않으면 커밋하지 않고, 커밋 메시지에 `Co-Authored-By`를 붙이지 않는다.
- 운영 DB는 읽기 전용이다. 새 SQL은 운영 DB에 `SELECT`로 한 번 돌려 컬럼·조인을 확인하고, DAG 실행·백필은 사용자에게 요청한다.

---

## 1. 결정 요약

구현 가능하다. 현재 프로젝트에는 필요한 토대가 대부분 있다.

| 필요한 것 | 현재 상태 | 결정 |
| --- | --- | --- |
| 국내 종목 일봉 OHLCV | `stock_investor_trade_daily`에 있음 | 그대로 재사용 |
| 국내 지수 일봉 OHLCV | 분봉만 있고 `index_daily`에는 없음 | KIS 일봉 API로 KOSPI·KOSDAQ만 추가 |
| 일봉 통합 저장소 | `IndexDaily`, `index_daily/upsert.sql`, `quote_daily` 뷰가 있음 | 새 스키마 없이 재사용 |
| 일봉 조회 툴 | `daily_history`가 있음 | 새 LLM 툴 대신 응답에 지표 추가 |
| 방향성 표현 | `thesis`가 상승·하락·횡보 확률과 Brier 채점을 이미 제공 | 지표는 입력 관측값으로만 사용 |
| 사용자 출력 | 한국장 Slack 표와 당일 분봉 차트가 있음 | Slack에 작은 지표 표 추가, 차트는 유지 |
| 매매 신호 | 없음. 지표값만으로는 "언제 교차했는지"가 남지 않음 | `technical_signal` 테이블 + 계산 DAG 추가(12절). 지표 시리즈에서 검출하므로 새 원천은 없다 |
| 신호 채점 | `thesis_outcome`이 지평별 Brier를 채점 | 같은 형태로 지평별 사후 수익률을 SQL로 본다. 채점 테이블·DAG는 두지 않는다 |
| LLM 추론 입력 | `observed_state`가 세션 종가·등락만 줌. 지표는 툴로만 | 추론 대상(KOSPI·KOSDAQ·watched)의 snapshot·최근 신호를 관측 상태에 **함께 싣고**(14.1절) 깊은 이력만 툴로 |
| LLM 근거·평가 | `thesis_evidence`는 문서·공시·매크로만 인용 가능 | 신호를 `technical_signal:<id>`로 인용하게 하고(14.3절), `input_state`·`thesis_evidence`로 "지표가 추론에 도움이 됐나"를 SQL로 잰다(14.4절) |

별도 `technical_snapshot` LLM 툴을 만들지 않는다. 현재 툴은 13개이고 실행당 호출 상한은 12회다. 같은 심볼의 일봉과 보조지표를 따로 호출하게 만들 이유가 없으므로 기존 `daily_history` 한 번으로 둘 다 돌려준다.

## 2. 범위

### 2.1 1차 릴리스 대상

- 지수: `KOSPI`, `KOSDAQ`
- 종목: KIS 확정 일봉이 실제로 수집되는 `instrument.is_watched = true` 국내 종목. 현재는 `005930`, `000660`이다.
- 주기: 확정 일봉
- 지표:
  - SMA 20일
  - SMA 60일
  - RSI 14일
  - MACD 12·26·9와 histogram
  - 당일 거래량 / 직전 20거래일 평균 거래량
- 매매 신호(12절): `sma_cross`(SMA20/60 골든·데드크로스), `macd_cross`(MACD 라인과
  시그널 라인 교차), `rsi_reversal`(RSI14의 30·70 재돌파)
- 소비자:
  - `ThesisToolbox.daily_history` — 지표 snapshot과 최근 신호
  - 한국장 Slack 브리핑의 기술적 관측 표 — 지표와 최근 신호 열
  - `technical_signal` 테이블 — 신호 이력과 채점 SQL의 원천

`daily_history`가 이미 조회할 수 있는 해외 `quote_daily` 심볼도 60봉 이상이면 같은 계산 결과를 받는다. 다만 신규 수집·Slack 노출·완료 기준은 위 국내 대상에만 건다.

`is_watched`만 켠다고 새 국내 종목의 원천 데이터가 생기지는 않는다. 신규 종목은 기존 카탈로그 대조 테스트가 요구하는 `DomesticStock`·`InvestorFlowStock`·`DartCompany`와 `quote_symbol`·`instrument` 시드를 먼저 같은 코드로 추가해야 한다. 이 계획은 이미 수집되는 watched 종목의 지표 노출까지만 다룬다.

### 2.2 1차 릴리스에서 하지 않는 것

- 자동주문, 포지션 크기, 손절·익절, 여러 신호를 합친 종합 점수
- 신호 발생 즉시 Slack 알림. 신호는 다음 브리핑 표와 추론 툴에서만 보인다
- 잡음이 많은 신호: 종가의 SMA20 돌파, SMA5/20 교차. 1차 세 신호의 적중률을 본 뒤 판단한다(12.6절)
- 새 웹 화면이나 전략 빌더, 백테스트 엔진
- 지표별 설정 UI와 사용자별 파라미터
- 볼린저밴드·스토캐스틱·ADX·ATR 등 추가 지표
- 캔들 차트, SMA 오버레이, RSI 보조 패널
- 지표값 전용 DB 테이블과 과거 스냅샷 저장(신호 사건만 저장한다)
- KIS 공식 Strategy Builder 전체 이식

기존 분봉 차트는 당일 흐름을 이미 보여 준다. 일봉 차트를 함께 넣으면 새 계열 모델·렌더러·Slack 업로드·실패 처리가 모두 늘어난다. 1차 릴리스는 숫자 표와 추론 품질을 먼저 검증한다.

## 3. 데이터 흐름

```text
KIS 국내주식업종기간별시세
  KOSPI / KOSDAQ
        │
        ▼
    index_daily ───────────────┐
                               │
stock_investor_trade_daily ────┼─> technical/select_history.sql
  watched 국내 종목            │       │
                               │       ▼
                               └─> modules/technical.py
                                        │
                  ┌─────────────────────┼─────────────────────┐
                  ▼                     ▼                     ▼
   ThesisToolbox.daily_history   MarketSummary.technicals   technical_signal_daily DAG
                  │                     │                     │  detect_signals()
                  │                     │                     ▼
                  │                     │              technical_signal 테이블
                  │                     │                     │
                  │◄────────────────────┼─────────────────────┤ 최근 신호
                  ▼                     ▼                     ▼
        상승/하락/횡보 가설     Slack 기술적 관측 표      지평별 사후 수익률 SQL
```

핵심 경계는 다음과 같다.

1. 수집기는 외부 응답을 검증해 원천 일봉만 저장한다.
2. SQL은 지수와 종목의 서로 다른 컬럼명을 한 모양으로 읽는다.
3. `modules/technical.py`는 DB·Airflow·LLM을 import하지 않는 순수 계산 모듈이다. 지표 snapshot과
   신호 검출이 **같은 시리즈 계산**을 쓴다. 두 벌이 되면 Slack의 SMA와 신호의 SMA가 어긋나는 날이 온다.
4. thesis만 방향 확률을 만든다. 기술지표 모듈과 Slack 표는 관측값을 해석하지 않는다. 신호는
   "교차가 일어났다"는 사건이지 "사라" 판정이 아니고, 그 사건이 유효했는지는 사후 수익률이 답한다.

## 4. KIS 지수 일봉 계약

공식 기준은 한국투자증권 Open Trading API 저장소의 `main`이다. 구현 시 다시 대조한다.

- [국내주식업종기간별시세 공식 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_indexchartprice/inquire_daily_indexchartprice.py)
- [공식 응답 필드 확인 예제](https://github.com/koreainvestment/open-trading-api/blob/main/examples_llm/domestic_stock/inquire_daily_indexchartprice/chk_inquire_daily_indexchartprice.py)
- [KIS Strategy Builder 지표 구현](https://github.com/koreainvestment/open-trading-api/blob/main/strategy_builder/core/indicators.py)

Strategy Builder는 기술지표를 시세 API 응답에서 받는 것이 아니라 OHLCV에서 로컬 계산하는 공식 선례로만 참고한다. pandas 기반 80개 지표와 주문 기능은 가져오지 않고, 5절에 고정한 다섯 계산만 구현한다.

### 4.1 요청

| 항목 | 값 |
| --- | --- |
| Method | `GET` |
| Path | `/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice` |
| TR ID | `FHKUP03500100` |
| `FID_COND_MRKT_DIV_CODE` | `U` |
| `FID_INPUT_ISCD` | KOSPI `0001`, KOSDAQ `1001` |
| `FID_INPUT_DATE_1` | 조회 시작일 `YYYYMMDD` |
| `FID_INPUT_DATE_2` | 조회 종료일 `YYYYMMDD` |
| `FID_PERIOD_DIV_CODE` | `D` |

인증·기본 헤더·`tr_cont` 요청은 기존 `modules.collectors.kis.send_get()`을 재사용한다. 첫 요청의 `tr_cont`는 빈 문자열이고 응답 헤더가 `M` 또는 `F`이면 다음 요청은 `N`이다. 페이지 사이는 기존 KIS 달력 수집기와 같은 0.5초를 기다리고 테스트는 `sleep=0`으로 없앤다. 공식 예제처럼 최대 10장까지만 허용하되, 열 번째 응답에도 다음 장이 있으면 그 심볼을 저장하지 않고 실패시킨다.

**연속조회가 실제로 되는지는 검증되지 않았다.** 같은 KIS의 확정 수급 일별 API는 `tr_cont`가 빈 문자열로 와서 연속조회가 없고, 한 응답이 30거래일로 잘린다(`collectors/market/kis_investor_flow.py`의 `DAILY_TRADE_ROWS_PER_CALL`, 실측). KIS 기간별 차트 API도 한 응답 100봉 상한이 흔하다. 구현 첫 단계에서 200달력일 구간을 한 번 요청해 **행 수와 응답 헤더 `tr_cont`를 실측**하고 둘 중 하나로 확정한다.

- `tr_cont`가 `M`/`F`로 오면 위 연속조회 그대로.
- 오지 않고 행이 잘리면 `kis_investor_flow.fetch_stock_trade_daily`처럼 **날짜 창을 뒤로 옮긴다.** 받은 가장 오래된 날짜의 전날을 다음 `FID_INPUT_DATE_2`로 쓰고, 창이 요청 시작일에 닿거나 빈 응답이 오면 멈춘다. 상한 10창은 같다.

어느 쪽이든 "마지막 장에도 더 있음"은 부분 저장이 아니라 실패다. 잘린 구간은 지표 계산 창에 구멍을 남긴다.

### 4.2 응답 매핑

`output2`에서 다음 필드만 읽는다.

| KIS 필드 | 저장 컬럼 |
| --- | --- |
| `stck_bsop_date` | `business_date` |
| `bstp_nmix_oprc` | `open` |
| `bstp_nmix_hgpr` | `high` |
| `bstp_nmix_lwpr` | `low` |
| `bstp_nmix_prpr` | `close` |
| `acml_vol` | `volume` |

`mod_yn`은 저장하지 않는다. 같은 `(provider, symbol, business_date)`가 다시 오면 기존 `index_daily/upsert.sql`이 최신 OHLCV와 `source_record_id`로 갱신한다.

### 4.3 검증

- `rt_cd == "0"`
- 날짜가 정확한 `YYYYMMDD`이고 요청 구간 안에 있음
- 날짜 중복 없음
- OHLC가 유한한 양수
- `high >= max(open, close, low)` 및 `low <= min(open, close, high)`
- 거래량이 0 이상
- 페이지 상한 뒤에도 연속조회 표식이 남으면 실패

한 지수가 실패해도 다른 지수는 저장한 뒤 DAG를 실패시킨다. 한 심볼의 모든 페이지는 파싱이 끝난 뒤 한 트랜잭션으로 저장하므로 그 심볼의 부분 일봉은 없다. 먼저 완료된 다른 심볼은 커밋돼 있을 수 있다. 401은 기존 KIS DAG와 같이 token cache를 `force=True`로 한 번 갱신해 해당 심볼을 한 번 재시도한다. 그 밖의 재시도 가능한 HTTP·연결 오류는 Airflow 재시도로 넘기고, 400·403·404와 응답 계약 위반은 즉시 실패한다.

### 4.4 수집 시각과 범위

- DAG: `kis_index_daily`
- 스케줄: `"20 18 * * 1-5"  # KST 월~금 18:20 = UTC 월~금 09:20`. 종목 확정 일봉
  `kis_investor_trade_daily`(18:10) 뒤, 신호 DAG(18:40, 12.3절) 앞이다.
- start date: `pendulum.datetime(2026, 8, 24, tz=KST_TIMEZONE)`
- 기본 조회: 실행일을 끝으로 최근 200일의 달력 구간
- 자동 실행: KRX 휴장일이면 skip
- 수동 실행: `end_date`를 주면 휴장일 여부와 관계없이 그 날짜까지 조회

200일은 SMA60과 EMA 안정화에 필요한 120거래일을 연휴가 포함된 구간에서도 확보하기 위한 고정 수집 창이다. 일별 호출 수는 KOSPI·KOSDAQ 두 심볼뿐이다.

## 5. 기술지표 계산 계약

### 5.1 입력과 출력

```python
from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, ConfigDict, Field

TECHNICAL_LOOKBACK_BARS = 120
TECHNICAL_MIN_BARS = 60

class DailyBar(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    business_date: date
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: int | None = Field(default=None, ge=0)

class TechnicalSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    subject_code: str
    label: str
    as_of_date: date
    close: float
    sma20: float
    sma60: float
    rsi14: float
    macd: float
    macd_signal: float
    macd_histogram: float
    volume_ratio20: float | None
    observations: int

def summarize(
    subject_code: str,
    label: str,
    bars: Sequence[DailyBar],
    *,
    max_abs_daily_change_pct: float | None = None,
) -> TechnicalSnapshot | None: ...
```

`summarize()`는 마지막 값만 돌려주지만 **내부는 시리즈 함수**로 짠다 — `sma_series(closes, n)`,
`ema_series(closes, n)`, `rsi_series(closes, n)`이 봉마다의 값을 돌려주고 snapshot은 그 마지막
원소다. 12.1절의 신호 검출이 같은 시리즈에서 직전 봉과 당일 봉을 비교하므로, 계산 코어가 한 벌이어야
Slack 표의 SMA와 신호의 SMA가 같은 값이다. 시리즈 함수는 창 앞부분의 미정의 구간을 `None`으로 채워
길이를 입력과 같게 한다 — 인덱스가 봉과 1:1이어야 날짜를 잘못 붙이지 않는다.

입력 날짜는 중복 없이 엄격한 거래일 오름차순이어야 한다. 60봉보다 적거나 날짜 순서가 어긋나면 snapshot을 만들지 않는다. KIS 국내 시계열은 `max_abs_daily_change_pct=35.0`을 넘는 인접 종가가 있으면 만들지 않는다. 현재 추적 종목에서 이 크기의 하루 단절은 기업행사 또는 원천 이상부터 확인해야 하므로, 이동평균을 그대로 보여 주는 것보다 누락시키는 편이 안전하다.

### 5.2 공식

- `SMA(n) = 마지막 n개 종가의 산술평균`
- RSI14:
  - 첫 14개 변화의 상승분·하락분 평균으로 시작한다.
  - 이후 `avg = (이전 avg × 13 + 오늘 값) / 14`인 Wilder 평활을 쓴다.
  - 상승·하락이 모두 0이면 50, 평균 하락이 0이면 100이다.
- EMA:
  - 첫 값은 해당 기간의 SMA로 시작한다.
  - 이후 `alpha = 2 / (period + 1)`, `EMA = alpha × close + (1-alpha) × 이전 EMA`다.
- `MACD = EMA12 - EMA26`
- `signal = MACD의 EMA9`
- `histogram = MACD - signal`
- `volume_ratio20 = 최신 거래량 / 최신일을 제외한 직전 20거래일 평균 거래량`
  - 21개 거래량 중 하나라도 없거나 직전 평균이 0이면 `None`이다.

계산은 반올림하지 않는다. JSON과 Slack 경계에서만 표시 자릿수를 줄인다. 라이브러리와 증권사마다 EMA 초기값과 RSI 평활 방식이 달라 값이 조금씩 다를 수 있으므로 위 공식과 고정 벡터 테스트가 이 프로젝트의 계약이다.

### 5.3 고정 벡터

종가와 거래량이 모두 `1, 2, ..., 120`인 120봉은 다음 결과를 내야 한다.

```text
SMA20 = 110.5
SMA60 = 90.5
RSI14 = 100.0
MACD = 7.0
MACD signal = 7.0
MACD histogram = 0.0
volume_ratio20 = 120 / 109.5 = 1.095890410958904
```

## 6. 조회 계약

본문 조회 SQL `airflow/sql/postgres/technical/select_history.sql`과 빈 결과 목록 SQL `select_symbols.sql` 두 개만 둔다.

```sql
WITH requested AS (
    SELECT unnest(%(symbols)s::text[]) AS symbol
    UNION
    SELECT ticker
    FROM instrument
    WHERE %(include_watched)s
      AND is_watched
      AND market IN ('kospi', 'kosdaq')
), normalized AS (
    SELECT daily.provider,
           daily.symbol,
           symbol.label,
           symbol.kind,
           symbol.country,
           daily.business_date,
           daily.open,
           daily.high,
           daily.low,
           daily.close,
           daily.volume,
           daily.created_at
    FROM quote_daily AS daily
    JOIN quote_symbol AS symbol
      ON symbol.provider = daily.provider
     AND symbol.symbol = daily.symbol
    JOIN requested ON requested.symbol = daily.symbol
    WHERE NOT (daily.provider = 'kis' AND symbol.kind = 'equity')

    UNION ALL

    SELECT daily.provider,
           daily.stock_code AS symbol,
           symbol.label,
           symbol.kind,
           symbol.country,
           daily.business_date,
           daily.open_price AS open,
           daily.high_price AS high,
           daily.low_price AS low,
           daily.close_price AS close,
           daily.accumulated_volume AS volume,
           daily.created_at
    FROM stock_investor_trade_daily AS daily
    JOIN quote_symbol AS symbol
      ON symbol.provider = daily.provider
     AND symbol.symbol = daily.stock_code
    JOIN requested ON requested.symbol = daily.stock_code
    WHERE daily.provider = 'kis'
), ranked AS (
    SELECT normalized.*,
           row_number() OVER (
               PARTITION BY provider, symbol
               ORDER BY business_date DESC
           ) AS position
    FROM normalized
    WHERE created_at <= %(as_of_at)s
)
SELECT provider, symbol, label, kind, country,
       business_date, open, high, low, close, volume
FROM ranked
WHERE position <= %(limit)s
ORDER BY symbol, business_date DESC
```

`daily_history`는 `symbols=[요청 심볼]`, `include_watched=false`로 호출한다. Slack 브리핑은 `symbols=["KOSPI", "KOSDAQ"]`, `include_watched=true`로 한 번 호출한다. 따라서 watched 종목이 늘어도 브리핑 코드를 바꾸지 않는다.

빈 결과에서 보여 줄 심볼 목록은 다음 `technical/select_symbols.sql`로 옮긴다.

```sql
WITH available AS (
    SELECT provider, symbol
    FROM quote_daily
    WHERE created_at <= %(as_of_at)s

    UNION

    SELECT provider, stock_code AS symbol
    FROM stock_investor_trade_daily
    WHERE provider = 'kis'
      AND created_at <= %(as_of_at)s
)
SELECT symbol.symbol, symbol.label, symbol.kind
FROM available
JOIN quote_symbol AS symbol
  ON symbol.provider = available.provider
 AND symbol.symbol = available.symbol
ORDER BY symbol.kind, symbol.symbol;
```

기존 `quote_daily/select_thesis_history.sql`과 `select_thesis_symbols.sql`은 참조를 바꾼 뒤 삭제한다.

## 7. 소비자 계약

### 7.1 `daily_history`

툴 이름과 인자는 유지한다.

```json
{
  "symbol": "KOSPI",
  "bars": [
    {"label": "코스피", "kind": "index", "country": "KR",
     "business_date": "2026-08-21", "open": 3190.1, "high": 3210.2,
     "low": 3184.4, "close": 3205.7, "volume": 412345678}
  ],
  "technical_snapshot": {
    "subject_code": "KOSPI",
    "label": "코스피",
    "as_of_date": "2026-08-21",
    "close": 3205.7,
    "sma20": 3160.2,
    "sma60": 3088.4,
    "rsi14": 61.3,
    "macd": 18.2,
    "macd_signal": 15.7,
    "macd_histogram": 2.5,
    "volume_ratio20": 1.12,
    "observations": 120
  },
  "recent_signals": [
    {"signal_date": "2026-08-19", "kind": "sma_cross", "direction": "up"},
    {"signal_date": "2026-08-12", "kind": "macd_cross", "direction": "down"}
  ]
}
```

- `days`는 기존처럼 1~30이며 `bars`도 요청 개수만 반환한다.
- `recent_signals`는 `technical_signal`에서 최근 60거래일, `created_at <= as_of_at`인 행을 최신순으로
  준다(12.5절). 신호가 없으면 빈 배열이다. 툴 설명은 "사건이지 판정이 아니다"와 "장후 슬롯은 당일
  신호를 아직 못 본다"를 적는다.
- 기존 bar의 `label`, `kind`, `country`, `business_date`, `open`, `high`, `low`, `close`, `volume` 키를 그대로 보존하고 최상위 `technical_snapshot`만 추가한다.
- 계산을 위해 내부 조회만 최대 120봉을 받는다.
- 60봉 미만·가격 단절이면 `technical_snapshot`은 `null`이고 원시 `bars`는 그대로 반환한다.
- 지표(`technical_snapshot`)는 문맥이므로 `Evidence.registry`에 넣지 않는다. `recent_signals`의 각 항목은 사건이라 `ref`(`technical_signal:<id>`)를 갖고 인용할 수 있다(14.3절).
- 새 툴이 아니므로 `MAX_TOOL_CALLS=12`, `MAX_TOOL_ROUNDS=3`은 바꾸지 않는다.
- 툴 설명과 출력 계약이 모델 입력을 바꾸므로 `PROMPT_VERSION`은 `2`에서 `3`으로 올린다.
- 장후 review의 `as_of_at`은 15:30이다. 18:10·18:20에 적재된 당일 종목·지수 일봉은 cutoff 뒤이므로 그 슬롯의 기술지표는 전일까지일 수 있다. `as_of_date`를 반드시 노출하고 최신인 척 보정하지 않는다.

### 7.2 Slack

`MarketSummary`에 다음 필드를 추가한다.

```python
technicals: tuple[TechnicalSnapshot, ...] = ()
signals: tuple[RecentSignal, ...] = ()   # 12.4절. symbol, signal_date, kind, direction
```

한국 프리마켓과 한국장 블록에서 기존 분봉 차트 다음에 표를 둔다. 미국장에는 넣지 않는다.

| 대상 | 종가/SMA20 | SMA20/SMA60 | RSI14 | MACD hist | 거래량/20일 | 신호 | 기준 |
| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |
| 코스피 | `+1.44%` | `+2.33%` | `61.3` | `+2.50` | `1.12x` | `골든크로스 08/19` | `08/21` |

- 비율은 `(왼쪽 / 오른쪽 - 1) × 100`이다.
- MACD histogram은 대상 가격 단위의 값을 부호와 함께 표시한다. 개별 종목은 원, 지수는 지수 포인트다.
- 거래량을 계산할 수 없으면 `-`다.
- `신호`는 최근 20거래일 안 가장 최근 사건 하나다. 표기는 12.4절의 사건 이름(`골든크로스`,
  `데드크로스`, `MACD↑`, `MACD↓`, `RSI 과매도 탈출`, `RSI 과매수 이탈`)과 발생일이다. 없으면 `-`.
- snapshot이 하나도 없으면 표 전체를 생략한다.
- `상승`, `하락`, `매수`, `매도`, 종합점수 같은 **판정** 열은 두지 않는다. 신호 열은 사건 이름이지
  판정이 아니다 — 같은 골든크로스가 좋은 신호였는지는 12.6절의 채점이 답한다.

### 7.3 방향성의 주체

보조지표가 직접 방향을 정하지 않는다. `ThesisBuilder`가 뉴스·공시·매크로·수급·과거 채점과 함께 기술지표를 보고 상승·하락·횡보 확률을 만든다. 기존 Brier 채점과 `prompt_version` 비교가 이 정보가 실제로 도움이 됐는지를 판단한다.

## 8. 운영·안전 계약

- 기술지표가 없어도 기존 시세·수급·분봉 차트·thesis는 계속 동작한다.
- 외부 API 응답 오류는 원천 수집 DAG를 실패시킨다. 실패한 심볼의 페이지 일부는 저장하지 않으며, 먼저 완료된 다른 심볼의 성공분은 유지한다.
- 지표 계산 불가 상태는 `null` 또는 Slack 행 생략으로 표현한다. 0으로 대체하지 않는다.
- source lineage는 지수별 `source_record`에 `source_key`, 조회 시작·종료일, page 수, 저장 행 수를 남긴다.
- 지수 일봉의 `source_key`는 공식 API 이름과 같은 `inquire_daily_indexchartprice`다.
- KIS 종목 확정 수급 API의 수정주가 의미는 현재 검증되지 않았다. 35% 연속 종가 단절 guard가 의심 구간의 지표 노출을 막는다. 실제 단절이 발견되기 전에는 별도 종목 일봉 API를 중복 수집하지 않는다.
- 롤백할 때 `kis_index_daily`를 pause하고 소비자 변경만 되돌린다. 이미 저장된 KIS `index_daily` 행은 다른 읽기 경로를 깨지 않으므로 삭제하지 않는다.
- 신호 층의 롤백도 같다. `technical_signal_daily`를 pause하고 Slack 열·툴 필드만 되돌린다. 테이블과 행은 남긴다.

---

## 9. 구현 순서

### Task 1: 순수 기술지표 계산기를 테스트부터 만든다

**Files:**

- Create: `tests/modules/test_technical.py`
- Create: `airflow/modules/technical.py`

- [ ] `test_technical.py`에 120봉 고정 벡터, 평평한 가격(RSI 50), 59봉 미만, 거래량 결측·0, 날짜 역순·중복, NaN·무한대, 35% 초과 가격 단절 테스트를 먼저 작성한다.
- [ ] 실패를 확인한다.

```bash
uv run pytest tests/modules/test_technical.py -q
```

- [ ] 5절의 `DailyBar`, `TechnicalSnapshot`, `summarize()`만 구현한다. 계산기는 SQL·Airflow·matplotlib을 import하지 않는다.
- [ ] 고정 벡터의 거래량 비율은 `assert snapshot.volume_ratio20 == pytest.approx(1.095890410958904, rel=1e-9, abs=1e-9)`로 고정한다.
- [ ] 테스트와 정적 검사를 통과시킨다.

```bash
uv run pytest tests/modules/test_technical.py -q
uv run ruff check airflow/modules/technical.py tests/modules/test_technical.py
```

### Task 2: KIS 지수 일봉 수집을 기존 collector에 추가한다

**Files:**

- Modify: `airflow/modules/collectors/kis.py`
- Modify: `tests/collectors/test_kis.py`
- Reuse unchanged: `airflow/sql/postgres/index_daily/upsert.sql`
- Reuse unchanged: `apps/models/market.py`

- [ ] 테스트에 공식 `output2` 모양의 KOSPI·KOSDAQ fixture를 추가한다.
- [ ] 요청 path·TR ID·다섯 query parameter, `tr_cont`의 `"" → "N"`, 페이지 사이 0.5초(`sleep=0` 테스트), 10장 상한, NaN·무한대 거절, 중복 날짜, 저장 컬럼 순서, source metadata를 검증한다.
- [ ] 실패를 확인한다.

```bash
uv run pytest tests/collectors/test_kis.py -q
```

- [ ] `DailyIndexBar`, `DailyIndexFetch`, `fetch_index_daily()`, `store_index_daily()`를 `kis.py`에 최소로 추가한다.
- [ ] 대상 Enum은 기존 `DomesticIndex`, 실제 순회 목록은 기존 `MOVEMENT_INDEXES`를 재사용한다.
- [ ] HTTP는 기존 `send_get()`, 저장은 기존 `INDEX_DAILY_UPSERT`와 `SOURCE_RECORD_INSERT`, 배치는 기존 `execute_upserts()`를 재사용한다.
- [ ] 모델 또는 마이그레이션을 만들지 않는다.
- [ ] 테스트와 lint를 통과시킨다.

```bash
uv run pytest tests/collectors/test_kis.py -q
uv run ruff check airflow/modules/collectors/kis.py tests/collectors/test_kis.py
```

### Task 3: 지수 일봉 DAG를 추가한다

**Files:**

- Create: `airflow/dags/kis_index_daily.py`
- Create: `tests/dags/test_kis_index_daily.py`

- [ ] DAG 테스트에 KST 18:20 평일 스케줄, aware KST start date, `max_active_runs=1`, `end_date` 형식 검증, 자동 휴장 skip, 수동 backfill 허용을 먼저 작성한다.
- [ ] 첫 401에서 `force=True` token 재발급 후 한 번 성공하는 경우와 두 번째 401이 그대로 올라가는 경우를 테스트한다.
- [ ] 실패를 확인한다.

```bash
uv run pytest tests/dags/test_kis_index_daily.py -q
```

- [ ] `end_date` 기본값은 기존 확정 수급 DAG처럼 `datetime.now(UTC).astimezone(KST_TIMEZONE).date()`로 정하고, 조회 시작은 `end_date - 200 days`로 구현한다.
- [ ] `start_date`는 `pendulum.datetime(2026, 8, 24, tz=KST_TIMEZONE)`로 고정한다.
- [ ] `MOVEMENT_INDEXES`를 순회하고 심볼마다 `atomic(connection)`으로 저장한다.
- [ ] 한 심볼 실패 뒤 다른 심볼 수집은 계속하되 마지막에 실패 목록을 `AirflowFailException`으로 올린다.
- [ ] `KisHTTPError.status == 401`이면 기존 `_fetch_with_retry` 패턴대로 `access_token(Variable, app_key, app_secret, force=True)`을 한 번 호출하고 해당 심볼을 한 번만 다시 받는다.
- [ ] 기존 KIS 자격 증명과 token Variable cache를 재사용한다.
- [ ] 테스트와 lint를 통과시킨다.

```bash
uv run pytest tests/dags/test_kis_index_daily.py -q
uv run ruff check airflow/dags/kis_index_daily.py tests/dags/test_kis_index_daily.py
```

### Task 4: 일봉 조회를 정규화하고 기존 `daily_history`에 지표를 붙인다

**Files:**

- Create: `airflow/sql/postgres/technical/select_history.sql`
- Create: `airflow/sql/postgres/technical/select_symbols.sql`
- Delete: `airflow/sql/postgres/quote_daily/select_thesis_history.sql`
- Delete: `airflow/sql/postgres/quote_daily/select_thesis_symbols.sql`
- Modify: `airflow/modules/thesis.py`
- Modify: `tests/modules/test_thesis.py`

- [ ] `test_thesis.py`에 국내 지수와 watched 종목이 같은 응답 계약으로 나오는지, raw bars는 요청한 `days`만 나오고 계산에는 120봉을 쓰는지, 60봉 미만이면 snapshot만 `null`인지 먼저 테스트한다.
- [ ] 기존 raw bar의 아홉 키(`label`, `kind`, `country`, `business_date`, `open`, `high`, `low`, `close`, `volume`)가 그대로 유지되는 회귀 테스트를 추가한다.
- [ ] `created_at > as_of_at` 행 제외와 `Evidence.registry == {}`를 유지하는 테스트를 추가한다.
- [ ] 툴 이름 집합이 기존 13개 그대로이고 호출 상한도 12인지를 검증한다.
- [ ] 실패를 확인한다.

```bash
uv run pytest tests/modules/test_thesis.py -q
```

- [ ] 6절 SQL을 추가하고 두 기존 SQL 참조를 새 경로로 옮긴다.
- [ ] `_tool_daily_history()`가 결과를 거래일 오름차순으로 변환해 `technical.summarize()`에 전달한 뒤 raw bars와 snapshot을 함께 반환하게 한다.
- [ ] 국내 KIS 행에만 `max_abs_daily_change_pct=35.0`을 전달한다.
- [ ] `DailyHistoryArgs`와 `TOOL_DESCRIPTIONS`에서 “KOSPI·KOSDAQ 일봉이 없다”는 문구를 제거하고 지표 계약을 적는다.
- [ ] `PROMPT_VERSION = "3"`으로 올린다.
- [ ] 테스트와 lint를 통과시킨다.

```bash
uv run pytest tests/modules/test_technical.py tests/modules/test_thesis.py -q
uv run ruff check airflow/modules/technical.py airflow/modules/thesis.py tests/modules/test_technical.py tests/modules/test_thesis.py
```

### Task 5: 한국장 Slack에 기술적 관측 표를 추가한다

**Files:**

- Modify: `airflow/modules/briefing/market.py`
- Modify: `tests/modules/test_briefing_market.py`

- [ ] 한국 프리마켓·한국장 scope에는 지표 표가 있고 미국장 scope에는 없는 테스트를 먼저 작성한다.
- [ ] 지표 0건이면 섹션 생략, 거래량 비율 `None`이면 `-`, 기준 거래일 표시, 기존 차트 블록 순서·실패 context 유지도 검증한다.
- [ ] 실패를 확인한다.

```bash
uv run pytest tests/modules/test_briefing_market.py -q
```

- [ ] `MarketSummary.technicals`와 `collect_summary()`의 통합 조회 한 번을 추가한다.
- [ ] subject별 행을 묶어 `technical.summarize()`를 호출한다.
- [ ] 기존 `blocks.table_section()`을 재사용하는 `_technical_section()`을 추가하고 7.2절의 열만 출력한다.
- [ ] `render_text()`와 분봉 `ChartSeries`·`render_series_png()`는 바꾸지 않는다.
- [ ] 테스트와 lint를 통과시킨다.

```bash
uv run pytest tests/modules/test_briefing_market.py tests/modules/test_briefing_chart.py -q
uv run ruff check airflow/modules/briefing/market.py tests/modules/test_briefing_market.py
```

### Task 6: 문서·전체 검증·초기 데이터 확보

**Files:**

- Modify: `docs/market-thesis/2-agent.md`
- Modify: `docs/market-thesis/TUNING.md`

- [ ] `daily_history` 행을 “일봉 + SMA20/60 + RSI14 + MACD + 거래량 비율”로 갱신하고 국내 일봉 부재 문구를 제거한다.
- [ ] TUNING 변경 이력에 `PROMPT_VERSION 2 → 3`, 기술지표 입력 추가, 비교 시작일을 기록한다.
- [ ] 전체 검증을 실행한다.

```bash
uv run pytest \
  tests/modules/test_technical.py \
  tests/collectors/test_kis.py \
  tests/dags/test_kis_index_daily.py \
  tests/modules/test_thesis.py \
  tests/modules/test_briefing_market.py \
  tests/modules/test_briefing_chart.py -q
uv run ruff check airflow tests
uv run pyrefly check
uv run pytest tests -q
```

- [ ] 배포 뒤 직전 영업일 기준으로 종목 150거래일과 지수 200달력일을 채운다. 아래 날짜는 이 문서 작성일(2026-08-23)의 직전 KRX 영업일이다.

```bash
airflow dags trigger kis_investor_trade_daily \
  --conf '{"end_date":"2026-08-21","pages":5}'
airflow dags trigger kis_index_daily \
  --conf '{"end_date":"2026-08-21"}'
```

- [ ] 운영 DB에서 각 대상이 120봉 이상인지 확인한다.

```sql
SELECT symbol, count(*) AS bars, min(business_date), max(business_date)
FROM index_daily
WHERE provider = 'kis' AND symbol IN ('KOSPI', 'KOSDAQ')
GROUP BY symbol;

SELECT stock_code, count(*) AS bars, min(business_date), max(business_date)
FROM stock_investor_trade_daily
WHERE provider = 'kis'
  AND stock_code IN (
      SELECT ticker
      FROM instrument
      WHERE is_watched AND market IN ('kospi', 'kosdaq')
  )
GROUP BY stock_code;
```

- [ ] 인접 종가가 35%를 넘는 국내 시계열이 있는지 확인한다. 결과가 있으면 그 subject의 지표가 노출되지 않는 것을 확인하고 원천 가격의 수정주가 의미를 조사한다.

```sql
WITH closes AS (
    SELECT stock_code,
           business_date,
           close_price,
           lag(close_price) OVER (PARTITION BY stock_code ORDER BY business_date) AS previous_close
    FROM stock_investor_trade_daily
    WHERE provider = 'kis'
)
SELECT stock_code, business_date, previous_close, close_price
FROM closes
WHERE previous_close > 0
  AND abs(close_price / previous_close - 1) > 0.35
ORDER BY stock_code, business_date;
```

- [ ] 코드 변경 뒤 프로젝트 지침대로 지식 그래프를 갱신한다.

```bash
graphify update .
```

Task 1~6이 지표 층이고 아래 Task 7~11이 신호 층(12절)이다. 신호 층은 Task 1(시리즈 함수)·Task 4(`technical/select_history.sql`)에 기대므로 순서를 바꾸지 않는다.

### Task 7: 신호 검출을 테스트부터 만든다

**Files:**

- Modify: `airflow/modules/technical.py`
- Modify: `tests/modules/test_technical.py`

- [ ] 고정 벡터를 먼저 쓴다. 종가가 60봉 내림(120→61) 뒤 60봉 오름(61→120)인 계단형에서 `sma_cross` up이 정확히 한 번, 계산한 날짜에 나는지. 5.3 단조 증가 벡터에서는 `sma_cross`·`macd_cross`가 0건이고 RSI가 100에 붙어 `rsi_reversal`도 0건인지. MACD 교차와 RSI 30 재돌파는 각각 작은 손제작 벡터로 잡는다. 같은 날 셋이 동시에 나도 셋 다 돌려주는지. `scan_bars=1`이면 마지막 봉만, `scan_bars=5`면 마지막 다섯 봉의 사건을 전부 돌려주는지. 60봉 미만·35% 단절이면 빈 리스트인지.
- [ ] 실패를 확인한다.

```bash
uv run pytest tests/modules/test_technical.py -q
```

- [ ] 12.1절의 `SignalKind`, `SignalEvent`, `detect_signals()`, `RULE_VERSION`, `RSI_OVERSOLD`, `RSI_OVERBOUGHT`를 구현한다. `summarize()`가 이미 쓰는 시리즈 함수를 그대로 쓴다. 5.3 고정 벡터 테스트가 그대로 통과해야 한다.
- [ ] 테스트와 lint를 통과시킨다.

```bash
uv run pytest tests/modules/test_technical.py -q
uv run ruff check airflow/modules/technical.py tests/modules/test_technical.py
```

### Task 8: `technical_signal` 모델과 수기 리비전

**Files:**

- Modify: `apps/models/analysis.py`
- Modify: `apps/models/__init__.py`
- Create: `migrations/versions/<rev>_add_technical_signal.py`
- Modify: `tests/models/test_analysis_models.py`
- Modify: `tests/migrations/` (기존 offline SQL 테스트 파일에 테이블 단위 사실 추가)

- [ ] offline SQL(`upgrade head --sql`)에 `CREATE TABLE technical_signal`, 테이블·컬럼 `COMMENT`, `ck_technical_signal_kind`·`ck_technical_signal_direction` CHECK, `uq_technical_signal_natural_key`가 나오는지 먼저 테스트한다. 리비전 ID에 고정하지 않는다.
- [ ] 실패를 확인한다.
- [ ] 12.2절대로 모델을 쓰고 `__all__`에 넣는다. 리비전은 손으로 쓴다 — `makemigrations`를 운영 DB에 돌리지 않는다. `downgrade_default()`는 `DROP TABLE`이다(이 프로젝트가 소유한다).
- [ ] 테스트와 lint를 통과시킨다.

```bash
uv run pytest tests/models/test_analysis_models.py tests/migrations -q
uv run ruff check apps migrations tests
```

### Task 9: 신호 저장 모듈과 DAG

**Files:**

- Create: `airflow/modules/technical_signals.py`
- Create: `airflow/sql/postgres/technical_signal/upsert.sql`
- Create: `airflow/dags/technical_signal_daily.py`
- Create: `tests/modules/test_technical_signals.py`
- Create: `tests/dags/test_technical_signal_daily.py`

- [ ] 모듈 테스트: `technical/select_history.sql` 결과를 subject별로 묶어 오름차순으로 `detect_signals()`에 넘기는지, upsert INSERT 컬럼과 `ON CONFLICT` 키가 `TechnicalSignal` 모델 metadata와 일치하는지(`tests/collectors/test_fred.py` 방식), 60봉 미만 subject는 건너뛰고 이름을 돌려주는지, 전부 건너뛰면 예외인지.
- [ ] DAG 테스트: 스케줄이 `"40 18 * * 1-5"`, `start_date`가 aware KST, `max_active_runs=1`, `scan_bars` Param의 범위·title·description, 자동 실행 휴장 skip, `dag_display_name`·`description`·`doc_md`가 비어 있지 않은지.
- [ ] 실패를 확인한다.
- [ ] 12.3절대로 구현한다. 휴장 guard와 연결·`atomic`은 `kis_index_daily`와 같은 것을 쓴다.
- [ ] 테스트와 lint를 통과시킨다.

```bash
uv run pytest tests/modules/test_technical_signals.py tests/dags/test_technical_signal_daily.py -q
uv run ruff check airflow/modules/technical_signals.py airflow/dags/technical_signal_daily.py tests
```

### Task 10: Slack 표에 신호 열을 붙인다

**Files:**

- Create: `airflow/sql/postgres/technical_signal/select_recent.sql`
- Modify: `airflow/modules/briefing/market.py`
- Modify: `tests/modules/test_briefing_market.py`

- [ ] 한국장 표에 `신호` 열이 있고 사건 이름·발생일 표기가 12.4절과 같은지, 20거래일 안 신호가 없으면 `-`인지, 미국장 scope에는 없는지, `signals`가 비어도 표의 나머지 열은 그대로인지 먼저 테스트한다.
- [ ] 실패를 확인한다.
- [ ] `MarketSummary.signals`, `collect_summary()`의 조회 한 번, `_technical_section()`의 열 추가를 구현한다.
- [ ] 테스트와 lint를 통과시킨다.

```bash
uv run pytest tests/modules/test_briefing_market.py -q
uv run ruff check airflow/modules/briefing/market.py tests/modules/test_briefing_market.py
```

### Task 11: `daily_history`에 최근 신호를 붙이고 문서를 맞춘다

**Files:**

- Create: `airflow/sql/postgres/technical_signal/select_thesis_recent.sql`
- Modify: `airflow/modules/thesis.py`
- Modify: `tests/modules/test_thesis.py`
- Modify: `docs/market-thesis/2-agent.md`
- Modify: `docs/market-thesis/TUNING.md`

- [ ] `recent_signals`가 7.1절 모양으로 나오고, `created_at > as_of_at` 행이 빠지고, 신호가 없으면 빈 배열이고, `Evidence.registry`는 비어 있고, 툴 수 13·호출 상한 12가 그대로인지 먼저 테스트한다.
- [ ] 실패를 확인한다.
- [ ] 12.5절대로 구현한다. Task 4와 같은 릴리스면 `PROMPT_VERSION`은 `"3"` 한 번만 올린다. 따로 나가면 `"4"`다.
- [ ] `2-agent.md`의 `daily_history` 행에 신호를 적고, `TUNING.md`에 `RULE_VERSION 1` 시작일을 적는다.
- [ ] Task 6의 전체 검증과 `graphify update .`를 다시 돌린다.
- [ ] 배포 뒤 백필은 사용자에게 요청한다. 지수·종목 일봉이 120봉 이상 채워진 뒤에 돌려야 한다.

```bash
airflow dags trigger technical_signal_daily --conf '{"scan_bars":120}'
```

- [ ] 운영 DB에서(SELECT만) 분포를 확인한다. 한 kind가 0건이거나 direction 한쪽만 있으면 검출 조건을 의심한다.

```sql
SELECT symbol, kind, direction, count(*) AS events, max(signal_date) AS latest
FROM technical_signal
GROUP BY symbol, kind, direction
ORDER BY symbol, kind, direction;
```

## 10. 완료 기준

- KIS KOSPI·KOSDAQ 일봉이 `index_daily`에 멱등 저장된다.
- 각 대상에 120봉이 확보되면 5개 지표가 5절의 고정 벡터와 같은 공식으로 계산된다.
- `daily_history` 한 번이 raw bars와 `technical_snapshot`을 함께 반환한다.
- LLM 툴 수와 호출 상한은 늘지 않는다.
- 한국장 Slack 표가 수치와 기준일만 표시하고 매수·매도 판단을 만들지 않는다.
- 결측·짧은 표본·가격 단절은 0이나 임의값이 아니라 snapshot 누락으로 드러난다.
- 기존 분봉 차트·시세·수급·thesis 채점 테스트가 모두 통과한다.
- 배포 4주 뒤 `prompt_version=2`와 `3`의 지평별 Brier 점수를 비교한다. 개선이 없으면 지표 종류를 늘리지 않고 툴 사용률과 reasoning 내 실제 사용 여부부터 확인한다.
- 세 신호가 `technical_signal`에 멱등 저장되고, 같은 일봉으로 다시 돌려도 행 수가 늘지 않는다.
- Slack `신호` 열과 `daily_history`의 `recent_signals`가 같은 테이블을 읽어 같은 사건을 보인다.
- 배포 4주 뒤 12.6절 SQL로 `kind·direction`별 지평 적중률을 본다. 그 결과가 신호를 늘리거나 줄이는 유일한 근거다.
- 관측 상태에 `technical` 블록이 실리고 `thesis.input_state`에 남는다. 모델이 신호를 인용하면 `thesis_evidence.evidence_kind = 'technical_signal'` 행이 생긴다(14절).
- 배포 4주 뒤 14.4절 세 SQL로 "지표가 추론에 도움이 됐나"를 `prompt_version`별로 비교한다. 개선이 없으면 push를 빼고 툴만 남긴다.

## 11. 후속 차트 도입 조건

다음 두 조건이 모두 충족될 때만 일봉 차트를 별도 계획으로 만든다.

1. 기술적 관측 표를 4주 운영해 실제로 보는 대상과 지표가 확정됐다.
2. 사용자가 숫자 표만으로 추세를 읽기 어렵다고 확인했다.

그때도 새 패키지를 넣지 않는다. 기존 matplotlib로 종가·SMA20·SMA60 상단 패널과 RSI14 하단 패널만 그리고, 현재 당일 분봉 차트는 그대로 둔다. 캔들·80개 지표·전략 빌더는 별도 요구가 생길 때 다시 설계한다.

## 12. 매매 신호 계약

2026-08-23 사용자 결정으로 추가했다. 이평선 매매·MACD 교차 같은 기법을 "수집·분석"하려면 지표값만으로는 부족하다 — 값은 매일 바뀌지만 **언제 교차했는지**는 남지 않는다. 그래서 교차를 사건으로 저장하고, 그 사건 뒤 실제로 어떻게 움직였는지를 채점한다. 채점이 규칙을 고치는 근거가 되는 구조는 `thesis`의 `prompt_version`·Brier와 같다.

### 12.1 신호 정의와 검출

1차는 셋이다. 5절 지표에서 바로 나오고 서로 다른 렌즈(추세·모멘텀·역추세)라 적중률 비교가 뜻이 있다.

| `kind` | 조건(직전 봉 → 당일 봉) | `direction` | 성격 |
| --- | --- | --- | --- |
| `sma_cross` | `SMA20 - SMA60` 부호가 음→양 / 양→음 | `up`(골든크로스) / `down`(데드크로스) | 추세추종, 느림, 드묾 |
| `macd_cross` | `MACD - signal`(= histogram) 부호가 음→양 / 양→음 | `up` / `down` | 모멘텀, 빠름 |
| `rsi_reversal` | RSI14가 30 아래→위 / 70 위→아래 | `up`(과매도 탈출) / `down`(과매수 이탈) | 역추세 |

- 부호 판정은 `> 0`과 `< 0`이다. 정확히 0은 어느 쪽도 아니고, 0을 사이에 둔 두 날은 교차가 아니다(0에 닿은 날과 다음 날 둘 다 사건이 안 된다). 지수 포인트·원 단위에서 정확히 0은 사실상 없으므로 이 규칙은 단순함을 위한 것이다.
- `direction`은 **사건의 방향**이다. 이것을 "매수·매도 판정"으로 읽는 것은 소비자 몫이고, 코드와 Slack은 사건 이름만 쓴다.
- 잡음 많은 종가의 SMA20 돌파, SMA5/20 교차는 넣지 않는다. 세 신호의 4주 적중률을 본 뒤 필요하면 `kind`를 늘린다(12.6절).

```python
SignalKind = StrEnum("SignalKind", {"SMA_CROSS": "sma_cross", "MACD_CROSS": "macd_cross", "RSI_REVERSAL": "rsi_reversal"})

RULE_VERSION = "1"          # 검출 규칙을 바꾸면 올린다. thesis의 PROMPT_VERSION과 같은 역할이다
RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0
SIGNAL_SCAN_BARS_MAX = 120  # TECHNICAL_LOOKBACK_BARS와 같다. 한 번에 다시 볼 수 있는 봉 수의 상한

class SignalEvent(BaseModel):
    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    signal_date: date
    kind: SignalKind
    direction: Literal["up", "down"]
    close: float
    sma20: float
    sma60: float
    rsi14: float
    macd: float
    macd_signal: float
    volume_ratio20: float | None
    rule_version: str

def detect_signals(
    bars: Sequence[DailyBar],
    *,
    scan_bars: int,
    max_abs_daily_change_pct: float | None = None,
) -> list[SignalEvent]: ...
```

- `summarize()`와 같은 시리즈 함수 위에서 돈다. 입력 조건(오름차순·중복 없음·60봉 이상·35% 단절 guard)도 같고, 조건에 안 맞으면 빈 리스트다.
- 마지막 `scan_bars`개 봉 각각에 대해 직전 봉과 비교한다. 같은 날 셋이 동시에 나면 셋 다 돌려준다.
- 사건 행에 당시 지표값을 함께 담는다. "거래량 동반 골든크로스만" 같은 사후 필터 분석이 SQL로 되게 하기 위해서다. `volume_ratio20`은 5.2절과 같이 계산 불가면 `None`이다.
- 계산은 반올림하지 않는다. DB 저장 시 Numeric 자릿수에서만 잘린다.

### 12.2 `technical_signal` 테이블

`apps/models/analysis.py`에 둔다. 추론의 입력이 되는 분석 산출물이라 `thesis`와 같은 모듈이다.

| 컬럼 | 타입 | 의미 |
| --- | --- | --- |
| `provider` | Text | 원천 제공처. 현재는 `kis`뿐이다 |
| `symbol` | Text | `KOSPI`·`KOSDAQ` 또는 6자리 종목코드. 마스터로 외래키를 걸지 않는다(`thesis.subject_code`와 같은 이유) |
| `signal_date` | Date | 사건이 난 KRX 거래일 |
| `kind` | `_enum_column(TechnicalSignalKind)` + CHECK | `sma_cross`·`macd_cross`·`rsi_reversal` |
| `direction` | `_enum_column(ThesisDirection)` + `CHECK direction IN ('up', 'down')` | 기존 enum 재사용. `flat`은 CHECK로 막는다 |
| `close`, `sma20`, `sma60`, `macd`, `macd_signal` | Numeric — `stock_investor_trade_daily`의 가격 컬럼과 같은 정밀도 | 사건 당시 값 |
| `rsi14` | Numeric(6, 2) | 0~100 |
| `volume_ratio20` | Numeric(10, 4), nullable | 계산 불가면 NULL |
| `rule_version` | Text | 검출 규칙 버전. 전후 비교의 축 |

- 자연키 `UniqueConstraint(provider, symbol, signal_date, kind)` — 이름 `uq_technical_signal_natural_key`.
- upsert는 `ON CONFLICT ... DO UPDATE`다. `thesis`는 첫 성공본이 불변이지만 이것은 결정적 계산이라, 원천 봉이 수정되면 값이 따라가야 맞다. 덮어써도 "최초 판단"이 사라지는 게 아니다. 이 차이를 docstring에 적는다.
- `source_record`를 남기지 않는다. 외부 응답이 아니라 파생 사건이다. 원천 계보는 `index_daily`·`stock_investor_trade_daily`의 `source_record_id`가 이미 갖는다.
- `created_at`(EntityBase)이 7.1절 툴의 `as_of_at` cutoff 기준이다.
- 테이블·컬럼 주석은 한국어로 모델과 리비전에 똑같이 넣는다. 리비전은 손으로 쓰고 offline SQL 테스트로 확인한다.

### 12.3 DAG `technical_signal_daily`

- `airflow/dags/technical_signal_daily.py`. 스케줄 `"40 18 * * 1-5"  # KST 월~금 18:40 = UTC 월~금 09:40`. 종목 일봉(18:10)·지수 일봉(18:20) 뒤다. 같은 날 두 앞단이 늦으면 다음 날 `scan_bars` 기본값이 메운다.
- `start_date = pendulum.datetime(2026, 8, 24, tz=KST_TIMEZONE)`, `max_active_runs=1`, `catchup=False`.
- 자동 실행은 KRX 휴장일이면 skip — `kis_index_daily`와 같은 guard를 쓴다. 수동 실행은 guard를 타지 않는다.
- Param `scan_bars`: 기본 5, 최소 1, 최대 `SIGNAL_SCAN_BARS_MAX`(120). `title`·`description` 필수. upsert라 재검출은 무해하므로 기본값을 1이 아니라 5로 둬서 앞단이 하루 늦게 복구돼도 사건이 빠지지 않는다. 초기 백필은 120.
- 조회는 6절 `technical/select_history.sql` 한 번 — `symbols=["KOSPI", "KOSDAQ"]`, `include_watched=true`, `as_of_at=now`, `limit=120`. 새 조회 SQL을 만들지 않는다.
- 새 모듈 `airflow/modules/technical_signals.py`가 "조회 → subject별 오름차순 정렬 → `detect_signals()` → `technical_signal/upsert.sql`"을 한다. 국내 KIS 행에는 `max_abs_daily_change_pct=35.0`을 준다(Task 4와 같음). subject마다 `atomic(connection)`이다.
- 실패 판정은 "항목별 실패 수집"이다. 60봉 미만 subject는 건너뛰고 이름을 모아 로그에 남긴다. **전부 건너뛰면 `AirflowFailException`** — 조용한 성공을 만들지 않는다. 계산·DB 예외는 그대로 올린다.
- `dag_display_name="📐 국내 기술적 매매 신호 (계산)"`, 한 문장 `description`, `doc_md=__doc__`.
- `technical.py`는 계속 DB·Airflow를 모른다. 연결을 쥐는 것은 `technical_signals.py`뿐이다.

### 12.4 Slack

7.2절 표의 `신호` 열이다. 조회 SQL `technical_signal/select_recent.sql`은 `(symbols, since_date)`를 받아 symbol마다 최신 사건 한 행을 준다. `since_date`는 브리핑 기준일의 20거래일 전 — 영업일 세기는 기존 `market_session/select_nth_open_day.sql`을 쓰고 SQL 두 곳에 나누지 않는다.

| `kind` + `direction` | 표기 |
| --- | --- |
| `sma_cross` up / down | `골든크로스` / `데드크로스` |
| `macd_cross` up / down | `MACD↑` / `MACD↓` |
| `rsi_reversal` up / down | `RSI 과매도 탈출` / `RSI 과매수 이탈` |

표기 사전은 `briefing/market.py`에 상수 하나로 둔다. `매수`·`매도` 낱말은 쓰지 않는다.

`MarketSummary.signals: tuple[RecentSignal, ...]`는 `symbol, signal_date, kind, direction` 넷뿐인 Pydantic 모델이다. 표 렌더러가 `technicals`와 symbol로 맞춘다. 미국장 scope에는 없다(7.2절과 같음).

### 12.5 thesis

7.1절 `recent_signals`다. 조회 SQL은 브리핑과 **별도 파일** `technical_signal/select_thesis_recent.sql`이다 — 툴은 `created_at <= as_of_at`로 걸고 브리핑은 지금까지를 본다. 기존 쿼리에 파라미터를 얹어 공유하지 않는 것은 프로젝트 규칙이다.

- 파라미터 `(symbol, as_of_at, since_date)`. `since_date`는 `as_of_at`의 60거래일 전.
- 툴 수·`MAX_TOOL_CALLS`·`MAX_TOOL_ROUNDS`는 그대로다. `TOOL_DESCRIPTIONS`의 `daily_history` 설명에 신호 세 종류와 "사건이지 판정이 아니다", "장후 슬롯(15:30)은 당일 신호를 아직 못 본다"를 적는다.
- snapshot은 `Evidence.registry`에 넣지 않는다(문맥). `recent_signals`는 항목마다 `ref`를 붙여 레지스트리에 넣는다 — 14.3절.
- `PROMPT_VERSION`은 Task 4와 같은 릴리스면 `"3"` 한 번만 올린다.

### 12.6 채점과 규칙 개정

채점 테이블·DAG를 두지 않는다. 사건과 일봉이 모두 DB에 있으므로 SQL 한 번이면 된다. 아래를 운영 4주 뒤 돌리고, 결과를 `docs/market-thesis/TUNING.md`에 `RULE_VERSION`별로 적는다.

```sql
-- kind·direction·rule_version별 T+N 거래일 사후 수익률. N은 거래일 수이지 달력일이 아니다.
-- 기준가는 사건일 종가(technical_signal.close)다. 사건일 종가로는 살 수 없지만 비교 기준은 하나여야 한다.
-- 주석에 퍼센트 기호를 쓰지 않는다. psycopg가 주석까지 훑어 플레이스홀더로 센다.
WITH bars AS (
    SELECT symbol, business_date, close,
           row_number() OVER (PARTITION BY symbol ORDER BY business_date) AS position
    FROM (
        SELECT symbol, business_date, close FROM index_daily WHERE provider = 'kis'
        UNION ALL
        SELECT stock_code, business_date, close_price FROM stock_investor_trade_daily WHERE provider = 'kis'
    ) AS daily
), anchored AS (
    SELECT signal.kind, signal.direction, signal.rule_version, signal.symbol, signal.signal_date,
           signal.close AS base_close, bars.position
    FROM technical_signal AS signal
    JOIN bars ON bars.symbol = signal.symbol AND bars.business_date = signal.signal_date
), horizons AS (
    SELECT unnest(ARRAY[1, 5, 20]) AS horizon
), scored AS (
    SELECT anchored.kind, anchored.direction, anchored.rule_version, horizons.horizon,
           (target.close - anchored.base_close) / anchored.base_close * 100 AS return_pct,
           CASE anchored.direction WHEN 'up' THEN target.close > anchored.base_close
                                   ELSE target.close < anchored.base_close END AS hit
    FROM anchored
    CROSS JOIN horizons
    JOIN bars AS target
      ON target.symbol = anchored.symbol
     AND target.position = anchored.position + horizons.horizon
)
SELECT kind, direction, rule_version, horizon,
       count(*) AS events,
       round(avg(CASE WHEN hit THEN 1 ELSE 0 END) * 100, 1) AS hit_rate_pct,
       round(avg(return_pct)::numeric, 2) AS mean_return_pct
FROM scored
GROUP BY kind, direction, rule_version, horizon
ORDER BY kind, direction, rule_version, horizon;
```

- 지평이 아직 안 온 사건은 그 지평 행에서 빠진다. 0으로 꾸미지 않는다.
- 적중률 50% 근처는 "정보 없음"이다. 표본이 수십 건이라 기준선과 구분하려면 지평 20의 평균 수익률까지 같이 본다.
- 규칙을 바꾸는 절차: `RULE_VERSION`을 올리고 → `scan_bars=120`으로 재검출해서 새 버전 행을 쌓고(자연키에 `rule_version`이 없으므로 옛 버전 행은 덮인다 — 옛 결과는 `TUNING.md`의 기록이 남는다) → 4주 뒤 다시 비교한다.
- 신호를 늘리는 조건은 하나다: 위 표로 세 신호의 적중률을 봤고, 늘리려는 신호가 기존 셋과 다른 렌즈라는 근거가 있다.

## 13. 2026-08-23 검토 기록

다른 태스크가 쓴 이 문서를 검토하며 고친 점이다. 설계 자체는 맞았고 아래는 사실 관계와 프로젝트 규칙에 맞춘 것이다.

| 위치 | 고친 것 | 이유 |
| --- | --- | --- |
| Task 6 검증 | `DJANGO_SETTINGS_MODULE=… manage.py check` 삭제 | 이 프로젝트는 Django가 아니다. `manage.py`가 없다 |
| 4.1 | 연속조회(`tr_cont`)가 된다는 가정을 "실측 뒤 확정"으로 바꾸고 날짜 창 이동 대안을 적음 | 같은 KIS 확정 수급 일별 API는 `tr_cont`가 빈 문자열이고 30거래일로 잘린다(`kis_investor_flow.py` 실측). 일봉 차트도 같은 식일 수 있다. 200달력일 구간이 한 장에 안 들어오면 조용히 구멍이 난다 |
| 4.4 | 스케줄을 cron 문자열 + UTC 병기로, `start_date`를 `pendulum.datetime(..., tz=KST_TIMEZONE)` 형태로 | 프로젝트 시간대 규칙. "KST 평일 18:20"만으로는 DAG에 옮길 때 UTC가 빠진다 |
| 5.1 | `summarize()`를 시리즈 함수 위에 두도록 명시 | 신호 검출이 같은 코어를 써야 Slack의 SMA와 신호의 SMA가 같다. 두 벌이면 어긋나는 날이 온다 |
| Global Constraints | "판정·새 테이블 금지"를 사용자 결정(매매 신호까지)에 맞게 고침. worktree·커밋·운영 DB 규칙 추가 | 원칙 변경은 문서 맨 위에 있어야 읽는 사람이 12절을 보기 전에 안다 |
| 2.2 | "종합 매매 신호"를 "종합 점수"로 좁히고 즉시 알림·잡음 신호·백테스트 엔진을 제외 목록에 추가 | 무엇을 안 하는지가 신호 층에서도 분명해야 한다 |
| 1·3·7·8·10절 | 신호 층의 행·흐름·필드·롤백·완료 기준 추가 | 12절과 대응 |
| 9절 | Task 7~11 추가 | 12절의 구현 순서. Task 1·4에 기대므로 뒤에 둔다 |

### 구현에서 설계와 달라진 것 (2026-08-24)

| 설계 | 구현 | 왜 |
| --- | --- | --- |
| 4.1 연속조회 판정 | `tr_cont`가 `M`/`F`면 연속조회, 헤더 없이 **요청 구간의 시작에 못 닿은** 응답이면 날짜 창을 뒤로 옮긴다 | 둘 다 다루는 한 함수로 만들었다. 처음에는 행 수가 가득 찼는지로 갈랐는데 그 상한(100행 가정)이 실제(50행)와 달라 잘림을 못 알아챘다 — 2026-08-25에 구간 기준으로 바꾸고 상수를 지웠다 |
| 12.4 신호 창 | 영업일 20일이 아니라 **달력 30일** | 표시용 칸이라 하루 이틀 경계가 흔들려도 읽는 사람의 판단이 안 바뀐다. 영업일 세기는 채점(12.6절)에만 쓴다 |
| 14.1 관측 상태 신호 조회 | 새 SQL 없이 툴의 `select_thesis_recent.sql`을 **대상마다 한 번씩** 부른다 | 대상이 두셋뿐이라 왕복을 아끼려고 파일을 하나 더 둘 값어치가 없다. 같은 SQL이라 cutoff 규칙이 어긋날 수 없다 |
| 14.1 신호 개수 | 관측 상태는 30일·최대 3건, 툴은 90일·`MAX_TOOL_RESULTS` | 관측 상태가 사건 목록으로 채워지지 않게 묶었다 |
| 12.3 실패 판정 | "60봉 미만은 건너뛴다"에 더해 **조회가 0행이어도 실패**시킨다 | 볼 대상이 하나도 없는 것은 앞단 수집이 빈 것이다. 건너뜀과 원천 부재를 다른 예외 메시지로 가른다 |
| Task 5 표 | `신호` 열을 Task 10에서 더했다 | 표 자체(Task 5)와 신호 열(Task 10)의 앞단이 달라 순서를 지켰다 |
| 관측 상태·과거 추론의 반환 타입 | `dict[str, Any]` → `thesis_state.py`의 Pydantic 모델(`ObservedState`·`NxtObservedState`·`TechnicalState`·`PastThesis` 등) | 사용자 요청(2026-08-24). 프롬프트와 JSONB 둘로 나가는 값이라 키 오타가 조용히 살아남으면 안 된다. 규칙은 `.claude/CLAUDE.md`·`.codex/AGENTS.md`의 "함수가 돌려주는 데이터 모양은 Pydantic 모델이다"에 적었고, 남은 곳도 2026-08-24에 함께 옮겼다(툴 응답은 `thesis_tools.py`) |
| `technical` 블록의 모양 | `{"as_of_date": ..., "KOSPI": {...}}` → `{"as_of_date": ..., "subjects": {"KOSPI": {...}}}` | 위 전환의 따라오는 변경. 기준일과 대상 코드가 같은 층에 섞이면 모델로 표현할 수 없다. 14.4절 평가 SQL의 경로도 `-> 'technical' -> 'subjects' -> subject_code`로 고쳤다 |

`ruff format`은 저장소가 강제하지 않는다(기존 파일 다수가 미포맷). 새로 만든 파일만 포맷했다.

검토에서 확인했지만 고치지 않은 것:

- 6절 `requested` CTE의 `market IN ('kospi', 'kosdaq')` — 이미 들어 있다. `instrument.market` 값은 `kospi`·`kosdaq`·`nyse`·`nasdaq`이다.
- `quote_symbol` 시드에 `kis/005930`·`kis/000660`이 `equity`로 있다. 6절 두 번째 UNION의 JOIN이 종목 행을 떨어뜨리지 않는다.
- `quote_daily` 뷰는 `created_at`을 갖는다(`e5b2d7a41c93` `DAILY_COLUMNS`). 6절의 cutoff가 동작한다.
- 5.3 고정 벡터는 손으로 다시 계산해 맞다. 선형 종가에 SMA 시드 EMA는 처음부터 정상 상태(`t - (n-1)/2`)라 MACD가 정확히 7.0이다.
- `MIN_HISTORY_DAYS=1`·`MAX_HISTORY_DAYS=30`(`thesis.py:184`), 툴 13개·`MAX_TOOL_CALLS=12`(`thesis.py:98`) — 7.1절과 같다.
- `kis_investor_trade_daily`의 `pages` Param은 최소 1이고 상한이 없다. Task 6의 `pages: 5`(150거래일)가 된다.

## 14. LLM 추론·평가에서 쓰는 방법

지표와 신호를 만들어 두는 것만으로는 추론이 나아지지 않는다. 모델이 **언제 무엇을 보고**, 그것을 **어떻게 인용하고**, 그 인용이 **실제로 도움이 됐는지**를 잴 수 있어야 한다. 세 층을 각각 정한다. 기준 구현은 `thesis_common.observed_state`(관측 상태), `ThesisToolbox`(툴·근거 레지스트리), `thesis_outcome`·`thesis_evidence`(채점·인용 기록)다.

### 14.1 추론 입력 — 관측 상태에 싣고(push), 이력은 툴로(pull)

지금 관측 상태(`thesis_common.py:162`)는 subject별 세션 종가·등락률만 준다. 지표는 7.1절대로 `daily_history` 툴로만 받는다. 그런데 **추론 대상이 곧 지표 대상**(KOSPI·KOSDAQ·watched 종목)이라, 툴로만 두면 모델이 실행당 12회 상한 중 대상 수만큼을 같은 조회에 쓰거나 아예 안 본다. 둘 다 나쁘다.

그래서 관측 상태에 `technical` 블록을 **함께 싣는다.**

```json
{
  "session": "2026-08-21",
  "index": {"KOSPI": {"close": 3205.7, "return_pct": 0.41}},
  "stock": {"005930": {"close": 71500.0}},
  "technical": {
    "as_of_date": "2026-08-21",
    "subjects": {
      "KOSPI": {
        "close_vs_sma20_pct": 1.44, "sma20_vs_sma60_pct": 2.33,
        "rsi14": 61.3, "macd_histogram": 2.5, "volume_ratio20": 1.12,
        "recent_signals": [
          {"ref": "technical_signal:1042", "signal_date": "2026-08-19", "kind": "sma_cross", "direction": "up"}
        ]
      },
      "005930": null
    }
  }
}
```

- 값은 7.2절 Slack 표와 **같은 다섯 칸**이다. 절대값(`sma20=3160.2`)이 아니라 비율로 준다 — 모델이 "종가가 SMA20 위인가"를 계산하지 않고 읽게 하기 위해서다. 절대값이 필요하면 툴이 있다.
- **모양은 `modules/thesis_state.py`의 Pydantic 모델이 정한다.** `ObservedState`·`TechnicalState`·
  `TechnicalObservation`·`SignalObservation`이고, JSON이 되는 것은 프롬프트 조립과 저장
  경계에서 `model_dump(mode="json")` 한 번뿐이다. 대상별 값이 `subjects` 아래로 한 단 들어간
  이유가 이것이다 — `as_of_date`와 대상 코드가 같은 층에 섞이면 모델로 표현할 수 없다.
- `as_of_date`는 블록에 하나다. 6절 cutoff(`created_at <= as_of_at`) 때문에 장후 슬롯(15:30)은 전일까지의 값이고, 장전(08:35)은 전 영업일 값이다. 프롬프트가 이 칸을 가리켜 "이 날짜 마감 기준"이라고 알린다(프로젝트 규칙 — 섞인 시간대·기준일은 프롬프트가 직접 알린다).
- 60봉 미만·가격 단절이면 그 subject의 `technical`은 `null`이다. 빈 dict나 0으로 채우지 않는다 — 모델이 "지표가 중립"으로 읽는다.
- `recent_signals`는 최근 20거래일이다(툴의 60거래일보다 짧다 — 관측 상태는 "지금 상태"고 툴은 "이력"이다).
- 계산은 `technical.summarize()`·`technical_signal/select_thesis_recent.sql`을 그대로 쓴다. `observed_state`가 커지므로 `technical_state(conn, session, as_of_at, targets)`를 `thesis_common.py`에 함수로 따로 두고 `observed_state`가 합친다. 슬롯으로 갈리지 않는다 — 세션·기준 시각은 부르는 쪽(`thesis_forecast.py:101`, `thesis_review.py:90`)이 이미 정해 넘긴다. `thesis_nxt_review`도 같은 함수를 쓴다(종목만).
- `daily_history` 툴은 그대로 둔다. 추론 대상이 아닌 심볼(해외 일봉), 30일 bars, 60거래일 신호 이력은 툴 몫이다.

관측 상태는 `thesis.input_state`(JSONB)에 그대로 저장된다. 그래서 **14.4절의 평가에 스키마 변경이 필요 없다** — 어떤 지표 체제에서 추론했는지가 행마다 남는다.

### 14.2 프롬프트 — 읽는 법을 알려 주되 결론을 주지 않는다

`SYSTEM_PROMPT`(`thesis.py:1399`)에 절 하나를 더한다. 초안:

```text
## 기술적 관측

관측 상태의 `technical`은 `as_of_date` 마감 기준의 일봉 지표다. 읽는 규칙:

- `close_vs_sma20_pct`, `sma20_vs_sma60_pct`가 둘 다 양수면 단기·중기 추세가 위(정배열),
  둘 다 음수면 아래(역배열)다. 부호가 갈리면 추세 전환 구간이다.
- `rsi14`는 70 위가 과열, 30 아래가 과매도다. 그 사이는 방향 정보가 약하다.
- `macd_histogram`은 부호가 모멘텀 방향, 크기 변화가 가속·감속이다.
- `volume_ratio20`이 1.5를 넘으면 그날 움직임에 거래가 실렸다는 뜻이고 0.7 아래면 실리지
  않았다는 뜻이다.
- `recent_signals`는 **교차가 일어났다는 사건**이다. 골든크로스가 곧 상승이 아니다.
  같은 사건이 과거에 얼마나 맞았는지는 너도 시스템도 아직 모른다.

지표는 **가격이 이미 한 일**이다. 왜 그랬는지는 말해 주지 않는다. 뉴스·공시·수급과
맞춰 보고, 맞는 것이 없으면 지표만으로 확률을 기울이지 마라. 횡보 이유에는 RSI 중립·
히스토그램 0 근처 같은 "방향 정보 없음"을 근거로 써도 된다.
```

- 임계(70/30, 1.5/0.7)는 코드 상수에서 f-string으로 싣는다. `RSI_OVERBOUGHT`·`RSI_OVERSOLD`는 12.1절 상수를 그대로 쓴다 — 검출 규칙과 프롬프트가 같은 숫자를 봐야 한다.
- "매수·매도 권유 금지"는 기존 규칙이 이미 막는다. 신호를 보고 "매수 신호가 났다"고 쓰는 것도 같은 규칙으로 막힌다 — 사건 이름(`골든크로스`)으로만 쓴다.
- 모델 입력이 바뀌므로 `PROMPT_VERSION`을 올린다. Task 4·11과 같은 릴리스면 `"3"` 한 번이다.

### 14.3 근거로 인용 — 신호는 `thesis_evidence`가 된다

지표값은 문맥이라 인용 대상이 아니다(7.1절). **신호는 사건이고 행 ID가 있어** 문서·공시처럼 인용할 수 있다. 인용하게 만드는 이유는 평가 때문이다 — "신호를 근거로 쓴 추론이 안 쓴 추론보다 나았나"를 재려면 어느 추론이 어느 신호를 인용했는지가 엣지로 남아야 한다.

- `ThesisEvidenceKind.TECHNICAL_SIGNAL = "technical_signal"`, `ref = technical_signal:<technical_signal.id>`. `thesis_evidence.ck_thesis_evidence_kind` CHECK에 값을 더한다 — Task 8의 리비전에 같이 넣는다.
- `Evidence` 행: `title`은 `코스피 골든크로스 (2026-08-19)`처럼 12.4절 사건 이름, `url`은 `None`, `detail`은 사건 당시 지표값(12.2절 컬럼 그대로).
- 레지스트리 등록은 두 경로다. `daily_history` 툴의 `recent_signals`(12.5절)와 관측 상태의 `technical.*.recent_signals`(14.1절). 관측 상태는 툴이 아니라서 `ThesisToolbox`에 `register(evidence: Iterable[Evidence])`를 두고 `build_and_store`가 관측 상태를 만든 직후 부른다. 레지스트리 밖의 ref는 저장 전에 버려지는 기존 규칙이 그대로 적용된다.
- 모델은 기존 `claims` 형식대로 `ref`·`direction`·`mechanism`을 쓴다. `mechanism`에 "골든크로스 뒤 추세추종 매수가 붙는 경로" 같은 한 문장이 온다. 이 칸이 있어야 그래프에서 `(:Thesis)-[:CITES {direction, mechanism}]->(:TechnicalSignal)`이 된다.
- 사후 해설(`FollowupNarrator`)은 바꾸지 않는다. 해설은 "원 추론의 이유가 **이후 보도**로 지지됐나"를 판정하는 것이라 지표가 들어갈 자리가 없다. 신호가 맞았는지는 12.6절이 LLM 없이 잰다.

### 14.4 평가 — 세 질문, 전부 SQL

기존 채점(`thesis_outcome`의 Brier, `grade_followups`)은 그대로다. 지표 층이 더하는 것은 아래 세 질문이고, 답은 전부 이미 저장되는 행에서 나온다. 새 테이블·DAG·LLM 호출이 없다.

**(1) 지표 체제별로 추론이 얼마나 맞았나** — `input_state`의 `technical` 블록으로 가른다.

```sql
-- 주석에 퍼센트 기호를 쓰지 않는다. psycopg가 주석까지 훑어 플레이스홀더로 센다.
SELECT outcome.horizon_days,
       CASE WHEN (tech ->> 'rsi14')::numeric >= 70 THEN 'rsi_hot'
            WHEN (tech ->> 'rsi14')::numeric <= 30 THEN 'rsi_cold'
            ELSE 'rsi_mid' END AS regime,
       count(*) AS graded,
       round(avg(outcome.brier_score), 3) AS mean_brier
FROM thesis
JOIN thesis_outcome AS outcome ON outcome.thesis_id = thesis.id AND outcome.evaluated_at IS NOT NULL
CROSS JOIN LATERAL (SELECT thesis.input_state -> 'technical' -> 'subjects' -> thesis.subject_code AS tech) AS t
WHERE thesis.prompt_version = '3'
  AND tech IS NOT NULL AND tech <> 'null'::jsonb
GROUP BY outcome.horizon_days, regime
ORDER BY outcome.horizon_days, regime;
```

같은 틀로 `sma20_vs_sma60_pct` 부호(정배열·역배열), `recent_signals`가 비었는지 아닌지로 가를 수 있다. `prompt_version`은 문자열이라 `>=` 비교를 쓰지 않는다(`'10' < '3'`). 비교할 버전을 `=`나 `IN`으로 적는다. 균등 확률 baseline 0.667과 `prompt_version=2`의 같은 지평 평균이 비교 대상이다.

**(2) 신호를 인용한 추론이 더 나았나** — `thesis_evidence` 엣지로 가른다.

```sql
WITH cited AS (
    SELECT thesis.id AS thesis_id,
           coalesce(bool_or(evidence.evidence_kind = 'technical_signal'), false) AS cited_signal
    FROM thesis
    LEFT JOIN thesis_evidence AS evidence
           ON evidence.thesis_id = thesis.id
          AND evidence.outcome_horizon_days IS NULL
    WHERE thesis.prompt_version = '3'
    GROUP BY thesis.id
)
SELECT outcome.horizon_days,
       cited.cited_signal,
       count(*) AS graded,
       round(avg(outcome.brier_score), 3) AS mean_brier
FROM cited
JOIN thesis_outcome AS outcome
  ON outcome.thesis_id = cited.thesis_id
 AND outcome.evaluated_at IS NOT NULL
GROUP BY outcome.horizon_days, cited.cited_signal
ORDER BY outcome.horizon_days, cited.cited_signal;
```

인용한 쪽이 Brier가 낮으면 신호가 정보를 더한 것이고, 같거나 높으면 모델이 사건을 결론으로 직결한 것이다 — 그러면 14.2절 프롬프트를 고치지 신호를 늘리지 않는다.

**(3) 인용할 때 붙인 방향이 맞았나** — `thesis_evidence.direction`과 `thesis_outcome.actual_outcome`을 대조한다. `evidence_kind = 'technical_signal'`로 걸면 "골든크로스를 `up`으로 읽은 인용"의 적중률이 나온다. 12.6절의 신호 자체 적중률과 나란히 놓으면 **모델이 신호를 잘 읽었는지**와 **신호가 좋았는지**가 갈린다.

판정 절차:

- 배포 4주 뒤 위 셋과 12.6절을 `docs/market-thesis/TUNING.md`에 `prompt_version`·`RULE_VERSION`별로 적는다.
- (1)·(2)에서 개선이 없으면 14.1절 push를 빼고 툴(pull)만 남기는 것이 1차 롤백이다. 테이블·DAG는 그대로다.
- (3)에서 모델의 읽기가 신호 자체보다 나쁘면 14.2절 문구를 고친다. 신호 종류를 늘리는 것은 12.6절 조건만 따른다.

### 14.5 구현 — Task 12·13

Task 11 뒤다. 둘 다 테스트 먼저.

**Task 12: 관측 상태 `technical` 블록과 프롬프트**

- Modify: `airflow/modules/thesis_common.py`, `airflow/modules/thesis.py`(`SYSTEM_PROMPT`), `tests/modules/test_thesis_common.py`, `tests/modules/test_thesis.py`
- [ ] `technical_state()`가 subject마다 14.1절 모양을 내고, 60봉 미만이면 `null`이고, `created_at > as_of_at` 봉·신호를 보지 않고, 세 슬롯 모듈이 같은 함수를 부르는지 먼저 테스트한다.
- [ ] `SYSTEM_PROMPT`에 임계 상수가 f-string으로 들어가고 `RSI_OVERBOUGHT`·`RSI_OVERSOLD`가 12.1절과 같은 객체인지 테스트한다.
- [ ] 구현 뒤 `tests/modules/test_thesis*.py` 통과. `PROMPT_VERSION`은 같은 릴리스면 `"3"` 유지.

**Task 13: 신호 인용**

- Modify: `airflow/modules/thesis.py`(`ThesisEvidenceKind`, `ThesisToolbox.register`, `_tool_daily_history`), `apps/models/analysis.py`(`ThesisEvidenceKind`, CHECK), Task 8 리비전, `tests/modules/test_thesis.py`, `tests/migrations/`
- [ ] `recent_signals` 항목이 `technical_signal:<id>` ref로 레지스트리에 들어가고, 모델이 그 ref를 `claims`에 쓰면 `thesis_evidence`에 `direction`·`mechanism`과 함께 저장되고, 레지스트리 밖 ref는 버려지는지 먼저 테스트한다.
- [ ] offline SQL에 `ck_thesis_evidence_kind`가 `technical_signal`을 포함하는지 테스트한다.
- [ ] `docs/market-thesis/2-agent.md`의 근거 종류 표에 `technical_signal`을 더한다.

완료 기준(10절에 더함):

- 관측 상태에 `technical` 블록이 있고 `thesis.input_state`에 그대로 남는다.
- 모델이 신호를 인용하면 `thesis_evidence.evidence_kind = 'technical_signal'` 행이 생긴다.
- 14.4절 세 SQL이 운영 DB에서 돈다(SELECT만).
