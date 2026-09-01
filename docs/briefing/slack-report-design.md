# Slack 정기 브리핑

- 기준일: 2026-08-25
- 상태: 구현 완료

수집 결과를 다시 저장하지 않고 PostgreSQL에서 읽어 Slack Block Kit 메시지로 보낸다. 숫자와
비교값은 SQL과 `airflow/modules/briefing/`이 만들며, 시장·운영 브리핑은 LLM을 사용하지
않는다. 문서 브리핑만 후보 선별에 LLM을 쓰고 실패하면 점수순으로 대체한다.

## 브리핑 목록

| DAG | 스케줄(KST) | 채널 | 역할 |
| --- | --- | --- | --- |
| `slack_kr_market_briefing` | 평일 08:10, 09:00, 10:00~19:00 매시, 15:35, 20:15 | `SLACK_CHANNEL_MARKET` | NXT 프리·애프터마켓과 KRX 장중·마감 브리핑 |
| `slack_us_market_briefing` | 화~토 08:00 | `SLACK_CHANNEL_MARKET` | 밤사이 미국장 마감과 전일 국내장 복기 |
| `slack_document_briefing` | 매일 08:00, 12:00, 15:30, 20:00 | `SLACK_CHANNEL_DOCUMENT` | 직전 발송 이후 평가 문서 집계·선별 |
| `slack_ops_briefing` | 매일 08:00 | `SLACK_CHANNEL_OPS` | 최근 24시간 수집 성공·실패·무소식·0건 |

각 DAG는 `catchup=False`, `max_active_runs=1`이고 발송 태스크 하나로 끝난다. 발송을 마지막에
두어 그 전 단계의 재시도가 중복 메시지를 만들지 않게 한다. Slack이 메시지를 받은 뒤 응답만
유실된 경우의 중복은 허용한다.

## 공통 처리 흐름

1. `CONNECTION_ID`가 가리키는 PostgreSQL에서 필요한 최신값과 직전값을 읽는다.
2. `airflow/modules/briefing/`에서 고정폭 표와 Block Kit 블록을 만든다.
3. `airflow/modules/slack.py`의 `post_message`로 보낸다.

`slack_sdk.WebClient`에는 SDK 재시도 핸들러를 붙이지 않는다. `ratelimited`,
`internal_error`, `service_unavailable`, `fatal_error`, `message_limit_exceeded`와 네트워크
오류는 `ConnectionError`로 올려 Airflow가 재시도한다. 인증·채널·블록 오류는 `SlackError`로
바꾸고 DAG가 즉시 실패시킨다. 토큰은 예외와 로그에 넣지 않는다.

## 시장 브리핑

`MarketBriefingReader`가 한 연결과 한 기준 시각으로 데이터를 한 번 읽고, `MarketScope`가
보일 섹션만 고른다. 값이 없으면 표를 만들지 않으며 오래된 값은 숨기지 않고 각 행의 기준
시각으로 드러낸다.

### 데이터와 비교 기준

| 입력 | 현재값과 함께 보이는 비교값 |
| --- | --- |
| `quote_bar`, `stock_bar` | 종가, 전일 종가, 등락, 거래소·기준 시각. 정규장 발송은 당일 시가와 시가 대비를 함께 |
| `indicator_observation` | 직전 관측 금리와 bp 변화, 금리 스프레드의 전일 변화 |
| `market_investor_flow_snapshot` | 직전 세션의 시장 수급 |
| `stock_investor_estimate_snapshot` | 직전 영업일의 종목 추정 수급 |
| `stock_investor_trade_daily` | 최근 확정 종목 수급 |
| `market_movement_snapshot` | 직전 세션의 상승·하락 종목 수 |
| `krx_market_funds_daily` | 직전 영업일의 고객예탁금·신용융자 잔고 |
| `krx_stock_short_sale_daily`, `krx_stock_securities_lending_daily` | 직전 수집일의 공매도·대차 |
| 일봉과 `technical_signal` | SMA20·SMA60, RSI14, MACD 히스토그램, 20일 거래량비, 최근 기술 사건 |

기술 표는 관측 사실만 보여 주며 매수·매도 판정을 만들지 않는다. 국내 종목은 최신 봉의
거래소를 함께 표시해 KRX 마감값과 NXT 애프터마켓 값을 구분한다.

**비교 기준은 발송 시각에 따라 하나 더 붙는다.** 개장 전 발송(08:10·09:00)은 전일 종가
대비만 보여 주고, 정규장 이후 발송(10:00~20:15)은 `시가`·`시가대비` 두 열을 더한다. 전일
대비는 어제부터의 누적이라 장이 열린 뒤 얼마나 움직였는지를 말해 주지 않는다. 시가를 뽑는
세션의 경계는 자정이 아니라 **KST 08:00**이다 — 국내 NXT 프리마켓과 CME 야간 세션이 둘 다
그 시각에 시작해서, 자정으로 자르면 새벽까지 이어지는 미국 선물 세션이 두 동강 난다.
국내 종목의 시가는 KRX 봉에서만 뽑는다(NXT 프리마켓 08:00 봉이 시가로 잡히지 않게 한다).
국내 지수선물처럼 단일가(08:30~09:00) 봉이 쌓이는 심볼은 그 봉이 시가다 — 실제 체결값이지만
KRX 고시 09:00 시가와는 다를 수 있다.

### 한국장 구성

10시 전 실행은 `KOREA_PREOPEN`, 이후는 `KOREA` 범위다. 범위는 늦게 재실행돼도 바뀌지
않도록 벽시계가 아니라 Airflow logical time으로 고른다.

| 범위 | 섹션 |
| --- | --- |
| 프리마켓 | 국내 종목, 차트, 기술적 관측, 환율, 증시자금, 공매도·대차, 시장 등락 종목 수 |
| 정규장·애프터마켓 | 국내 지수·선물, 차트, 기술적 관측, 장중 해외, 환율, 시장 수급, 종목 추정·확정 수급, 증시자금, 공매도·대차, 시장 등락 종목 수 |

08:10과 09:00에는 NXT 프리마켓(08:00~08:50)의 삼성전자·SK하이닉스 봉을 사용한다.
15:30 이후 종목 최신값은 NXT 봉을 우선하며, 20:05 확정 분봉 배치 뒤 20:15 메시지가 하루를
마감한다. `market_session`에서 KRX가 확정 휴장이면 건너뛰고, 달력 판정이 없으면 발송한다.

### 차트

한국장 메시지는 다음 다섯 대상을 순서대로 그린다.

- KOSPI, KOSDAQ, 삼성전자(`005930`), SK하이닉스(`000660`), USD/KRW

대상마다 당일 분봉 이미지와 확정 일봉 이미지가 각각 생긴다. 당일 차트는 봉에서 실제 간격을
계산하고 시장·거래소·기준일을 제목에 표시한다. 일봉 차트는 500봉으로 지표를 계산하되 최근
60봉만 표시한다. 지수·종목은 캔들, SMA 5/20/60/120, RSI14, MACD를 그리고 환율은 종가선과
이동평균선만 그린다.

이미지는 `files_upload_v2`로 먼저 올리고 Slack 처리 대기 뒤 메시지 image 블록에 붙인다.
한 장이라도 실패하면 일부 차트만 보이는 상태를 피하려고 이미지 전체를 버리고 실패 사유를
본문에 남긴다. 봉이 없으면 정상 생략한다. 표 발송은 차트 실패와 무관하게 계속된다.

### 미국장 구성

뉴욕 기준 직전 세션이 확정 휴장이면 건너뛰고, 달력 판정이 없으면 발송한다. 섹션은 미국
지수·선물, 원자재, 크립토, ADR, 주요국 10년 금리, 금리 스프레드, 전일 국내 지수·선물,
시장·종목 수급이다. 미국장 메시지는 현재 차트를 첨부하지 않는다.

## 문서 브리핑

직전 발송 슬롯 이후 `assessed_at` 기준으로 평가된 문서를 읽는다. 점수로 후보 60건을 자른 뒤
`DocumentPicker`가 읽을 것과 주의할 것을 고른다. 목록 밖 문서 ID는 버리고, 선별이 실패하면
점수순 상위 5건으로 대체하면서 실패 사유를 표시한다.

평가 문서가 0건이어도 하트비트 메시지는 보내되 LLM은 호출하지 않는다. 이 DAG는
`source_record`를 쓰지 않는 `document_assessment_hourly`의 생존 신호도 겸한다.

## 운영 브리핑

`source_record`의 최근 24시간을 소스별로 집계해 실행 수, 실패 수, 저장 건수, 마지막 완료 후
경과 시간을 표시한다. 기대 소스와 비교해 무소식(창 안에 한 번도 안 돎)과 0건(성공으로
돌았는데 평일 하루 종일 한 건도 안 남김)을 찾고 최근 실패 5건을 덧붙인다. 문서 피드는
DB가 정하는 동적 목록이라 `문서 피드(N)` 한 줄로 접는다. 문서 평가는 대기 건수가 200건을
넘을 때만 경고한다.

정상이어도 매일 보낸다. 운영 감시가 LLM 장애에 같이 흔들리지 않도록 모델을 호출하지 않는다.

## 운영 준비물

- `SLACK_BOT_TOKEN`
- `SLACK_CHANNEL_MARKET`, `SLACK_CHANNEL_DOCUMENT`, `SLACK_CHANNEL_OPS`
- `CONNECTION_ID`가 가리키는 Airflow 연결
- 문서 선별용 `XAI_API_KEY`
- Slack Bot scopes: 메시지용 `chat:write`, 차트용 `files:write`
- 운영 Airflow 이미지의 `slack-sdk`, `matplotlib`, 한글 폰트(`fonts-nanum`)

비공개 채널에는 봇을 초대해야 한다. 공개 채널도 초대해 두면 권한 동작이 단순해진다.

## 구현과 검증 위치

| 영역 | 구현 | 테스트 |
| --- | --- | --- |
| Slack 전송 | `airflow/modules/slack.py` | `tests/modules/test_slack.py` |
| 시장 조회·렌더링 | `airflow/modules/briefing/market.py` | `tests/modules/test_briefing_market.py` |
| 차트 | `airflow/modules/briefing/chart.py` | `tests/modules/test_briefing_chart.py` |
| 문서 선별 | `airflow/modules/briefing/documents.py`, `picks.py` | `tests/modules/test_briefing_documents.py`, `test_briefing_picks.py` |
| 운영 집계 | `airflow/modules/briefing/ops.py` | `tests/modules/test_briefing_ops.py` |
| DAG 계약 | `airflow/dags/slack_*_briefing.py` | `tests/dags/test_slack_market_briefing.py` |
