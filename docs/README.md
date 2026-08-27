# docs 색인

주제별로 나눠 둔다. 문서마다 상태를 함께 적어, 읽는 사람이 "이게 지금 도는 코드인가"를
파일을 열기 전에 안다.

**지금 무엇이 남았나는 여기 적지 않는다.** 그 원본은 `docs/working/implementation-gaps.md`다
(추적하지 않는 파일이라 이 저장소에는 없다). 목록을 두 벌 관리하면 반드시 어긋난다.

## `collection/` — 외부에서 데이터를 가져오는 계약

| 문서 | 무엇 | 상태 |
| --- | --- | --- |
| [kis-market-data-collection.md](collection/kis-market-data-collection.md) | KIS 수급·포지션·캘린더 다섯 DAG | 구현 완료 |
| [kis-semiconductor-minute-bars.md](collection/kis-semiconductor-minute-bars.md) | 삼성전자·SK하이닉스 KRX·NXT 1분봉. KIS REST·WebSocket 계약의 원본 | 구현 완료. 9절 백필 DAG만 미구현 |
| [kis-overseas-index-close.md](collection/kis-overseas-index-close.md) | 미국 현물 지수 마감 분봉과 미국장 브리핑 섹션 분리 | 구현 완료 |
| [kis-program-trading.md](collection/kis-program-trading.md) | 프로그램매매 수급. TR ID·필드 매핑표가 값어치다 | **미구현.** 착수 게이트는 누적/증분 프로브 |
| [dart-disclosure-earnings.md](collection/dart-disclosure-earnings.md) | DART 공시와 잠정실적 숫자 추출 | 구현 완료 |
| [ecb-convergence-monthly.md](collection/ecb-convergence-monthly.md) | 유로 회원국 10년물 월평균 | 구현 완료 |
| [us-macro-indicators.md](collection/us-macro-indicators.md) | FRED 물가·실물 다섯 계열. `indicator_series`가 금리 전용에서 벗어난 경위 | 구현 완료 |
| [policy-rate-collection.md](collection/policy-rate-collection.md) | 국채 수집국 중앙은행 다섯의 정책금리. `kind='policy_rate'`를 더한다 | **미구현. 구현 계약.** 계열 ID 실측이 선행 |

## `analysis/` — LLM 평가·기술지표·시장 추론

| 문서 | 무엇 | 상태 |
| --- | --- | --- |
| [economic-document-archive-design.md](analysis/economic-document-archive-design.md) | 문서 아카이브 4단계 설계 | 1·2단계 완료, 3·4단계 재계획 보류 |
| [document-assessment-workflow.md](analysis/document-assessment-workflow.md) | `document_assessment_hourly`의 LangGraph 흐름도 | 구현 완료 |
| [market-technical-indicators.md](analysis/market-technical-indicators.md) | SMA·RSI·MACD 관측값과 매매 신호 검출·채점 | 구현 완료. 남은 것은 적중률 관측 |
| [market-episode-analysis.md](analysis/market-episode-analysis.md) | 일봉 변화·추정 매물대·시장 근거를 연결하는 `MarketEpisode` 설계 | **미구현. 구현 계약** |
| [market-thesis/](analysis/market-thesis/README.md) | 시장 추론 기록. 단계마다 문서 하나 | 1·2·3·5~11·13단계 완료, 4(그래프)·12(API)·14(웹 화면)는 미착수 — 그 README가 원본 |

## `briefing/` — 읽어서 내보내기만 하는 DAG

| 문서 | 무엇 | 상태 |
| --- | --- | --- |
| [slack-report-design.md](briefing/slack-report-design.md) | Slack 정기 브리핑 넷 | 구현 완료 |
| [disclosure-briefing.md](briefing/disclosure-briefing.md) | 새 공시가 들어오면 알린다 | 구현 완료 — 마이그레이션 없음, 코드 배포만 |

## `convention/` — 저장소 규약

| 문서 | 무엇 | 상태 |
| --- | --- | --- |
| [collectors-class-migration.md](convention/collectors-class-migration.md) | 무엇을 클래스로 묶고 어디서 파일로 가르는가 | 전환 완료. 새 코드가 따르는 형태 |

## 루트

| 문서 | 무엇 |
| --- | --- |
| [collection-map.html](collection-map.html) | 지금 무엇을 얼마나 자주 수집하는지 한 장 |
| [project-presentation.html](project-presentation.html) | 비개발자용 프로젝트 소개 슬라이드 |

## 문서를 쓸 때

- **머리에 상태를 적는다.** `구현 완료` / `미구현` / `보류`와 그 근거가 되는 파일 이름이다.
  상태 없는 문서는 다음 사람이 현재형으로 읽는다.
- **코드 경로를 적었으면 그 경로가 살아 있어야 한다.** 모듈이 갈리거나 폴더가 바뀌면 문서도
  같은 커밋에서 고친다. 죽은 경로는 없는 것보다 나쁘다 — 찾다 포기하기 전까지 시간을 먹는다.
- **끝난 계획 문서는 지우지 말고 상태를 바꾼다.** "왜 그렇게 했나"가 남는다. 지우는 것은
  그 값어치가 다른 문서로 옮겨 갔을 때뿐이다.
