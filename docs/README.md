# docs 색인

주제별로 나눠 둔다. 문서마다 상태를 함께 적어, 읽는 사람이 "이게 지금 도는 코드인가"를
파일을 열기 전에 안다.

**지금 무엇이 남았나는 여기 적지 않는다.** 그 원본은 `docs/working/implementation-gaps.md`다
(`.gitignore`의 `docs/working/`이라 커밋되지 않는다 — 로컬에는 있다).
목록을 두 벌 관리하면 반드시 어긋난다.

## `collection/` — 외부에서 데이터를 가져오는 계약

| 문서 | 무엇 | 상태 |
| --- | --- | --- |
| [kis-market-data-collection.md](collection/kis-market-data-collection.md) | KIS 수급·포지션·캘린더 다섯 DAG | 구현 완료 |
| [kis-semiconductor-minute-bars.md](collection/kis-semiconductor-minute-bars.md) | 삼성전자·SK하이닉스 KRX·NXT 1분봉. KIS REST·WebSocket 계약의 원본 | 구현 완료. 9절 백필은 코드 없이 해소(2026-08-27) |
| [kis-overseas-index-close.md](collection/kis-overseas-index-close.md) | 미국 현물 지수 마감 분봉과 미국장 브리핑 섹션 분리 | 구현 완료 |
| [kis-index-daily-collection.md](collection/kis-index-daily-collection.md) | 빠진 국내지수·국내선물·미국 현물지수 다섯 개의 KIS 일봉 수집 | 구현 완료. 운영 백필까지 끝났다(2026-08-28). 선물 백필 하한 2025-12-12은 KIS 만기물 보관 한계다 |
| [kis-program-trading.md](collection/kis-program-trading.md) | 프로그램매매 수급. TR ID·필드 매핑표가 값어치다 | **미구현.** 착수 게이트는 누적/증분 프로브 |
| [dart-disclosure-earnings.md](collection/dart-disclosure-earnings.md) | DART 공시와 잠정실적 숫자 추출 | 구현 완료 |
| [ecb-convergence-monthly.md](collection/ecb-convergence-monthly.md) | 유로 회원국 10년물 월평균 | 구현 완료 |
| [us-macro-indicators.md](collection/us-macro-indicators.md) | FRED 물가·실물 다섯 계열. `indicator_series`가 금리 전용에서 벗어난 경위 | 구현 완료 |
| [policy-rate-collection.md](collection/policy-rate-collection.md) | 국채 수집국 중앙은행 다섯의 정책금리. `kind='policy_rate'`를 더한다 | 구현 완료(2026-08-27). `policy_rate_weekly` |
| [central-bank-assets-collection.md](collection/central-bank-assets-collection.md) | 중앙은행 여섯의 대차대조표 총자산. `kind='balance_sheet'`와 그 항목 | 구현 완료(2026-08-28). `central_bank_assets_weekly` |
| [korea-trade-collection.md](collection/korea-trade-collection.md) | 관세청 10일 단위 수출입 잠정치 42계열 | 구현 완료(2026-08-28). `kcs_trade_daily` |
| [document-body-collection.md](collection/document-body-collection.md) | 문서 본문·첨부 파일·영상 링크. 검색이 딛고 설 원문을 모은다 | 구현 완료(2026-08-30). `document_body_hourly`. **파일 마운트 뒤에 배포** |

## `analysis/` — LLM 평가·기술지표·코스피 전망

| 문서 | 무엇 | 상태 |
| --- | --- | --- |
| [economic-document-archive-design.md](analysis/economic-document-archive-design.md) | 문서 아카이브 4단계 설계 | 1·2단계 완료. 6.6 섹터 확장 2026-08-31 적용 완료, **6.7 업종 축 미구현**. 3·4단계 재계획 보류 |
| [document-assessment-workflow.md](analysis/document-assessment-workflow.md) | `document_assessment_hourly`의 LangGraph 흐름도 | 구현 완료 |
| [pdf-parsing-bm25.md](analysis/pdf-parsing-bm25.md) | PyMuPDF 첨부 파싱 → 첨부 텍스트 → 문서 단위 BM25 색인. 외부 호출·임베딩 없음 | **구현 완료, 배포 대기** |
| [pdf-vision-analysis.md](analysis/pdf-vision-analysis.md) | 텍스트가 안 나오는 영역만 외부 Vision에 보내는 설계 | **보류.** 조건이 관측되면 켠다 |
| [market-technical-indicators.md](analysis/market-technical-indicators.md) | SMA·RSI·MACD 관측값과 매매 신호 검출·채점 | 구현 완료. 남은 것은 적중률 관측 |
| [market-episode-analysis.md](analysis/market-episode-analysis.md) | 일봉 변화·추정 매물대·시장 근거를 연결하는 `MarketEpisode` 설계 | **미구현. 구현 계약** |
| [kospi-forecast.md](analysis/kospi-forecast.md) | 코스피 일일 전망. 관계 그래프(Neo4j)·메모·툴 셋으로 슬롯 셋(장전·장중·마감전)의 방향·등락률·±폭과 이유를 낸다 | **운영 중**(2026-09-03 기동). §8.7(아시아 요인)만 미구현. 옛 `market-thesis/`·`market-causal-graph.md`와 그 코드·표는 같은 날 지웠다 |
| [kospi-evaluation.md](analysis/kospi-evaluation.md) | **그 전망이 실제로 맞나.** 기준선, 언제 무엇을 보나, 어떤 숫자에 무엇을 정하나. SQL이 문서 안에 있어 이것 하나로 채점이 끝난다 | **동결 중.** 판 4를 2026-09-03부터 20영업일 안 고친다 |

## `issues/` — 운영 이슈와 복구

| 문서 | 무엇 | 상태 |
| --- | --- | --- |
| [2026-09-01-document-attachment-bm25-mutable-segment.md](issues/2026-09-01-document-attachment-bm25-mutable-segment.md) | BM25 mutable 세그먼트 검색 지연. 두 인덱스를 `mutable_segment_rows=0`으로 끄고 VACUUM으로 적체를 없앤다 | **해결. 리비전 `70e8e9ce64d3` 반영 대기**(2026-09-02) |

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
| [operations.md](operations.md) | 설정·DB alias·마이그레이션·DAG 목록·배포·관측. 저장소를 돌리는 쪽이 읽는다 |
| [collection-map.html](collection-map.html) | 지금 무엇을 얼마나 자주 수집하는지 한 장 |
| [project-presentation.html](project-presentation.html) | 비개발자용 프로젝트 소개 슬라이드 |

## 문서를 쓸 때

- **머리에 상태를 적는다.** `구현 완료` / `미구현` / `보류`와 그 근거가 되는 파일 이름이다.
  상태 없는 문서는 다음 사람이 현재형으로 읽는다.
- **코드 경로를 적었으면 그 경로가 살아 있어야 한다.** 모듈이 갈리거나 폴더가 바뀌면 문서도
  같은 커밋에서 고친다. 죽은 경로는 없는 것보다 나쁘다 — 찾다 포기하기 전까지 시간을 먹는다.
- **끝난 계획 문서는 지우지 말고 상태를 바꾼다.** "왜 그렇게 했나"가 남는다. 지우는 것은
  그 값어치가 다른 문서로 옮겨 갔을 때뿐이다.
