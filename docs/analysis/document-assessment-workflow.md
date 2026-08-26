# 경제 문서 LLM 평가 워크플로우

`document_assessment_hourly`는 `dedup >> evaluate` 두 태스크다. `evaluate`가 두 개의 LangGraph를 실행하고,
LangChain의 `ChatOpenAI`로 gpt-5.6-luna를 호출한다.

```mermaid
flowchart TD
    subgraph AIRFLOW["Airflow · document_assessment_hourly"]
        TIMER(["매시 25분 실행"]) --> DEDUP["dedup<br/>같은 기사를 대표에 연결"]
        DEDUP -- "all_done · 실패해도 진행" --> EVALUATE["evaluate"]
        EVALUATE --> SETTINGS["환경 설정 로드<br/>관점 · 최대 동시 실행 수"]
        SETTINGS --> LOAD["DB 조회<br/>평가 대상 문서 · 허용 태그 후보"]
        LOAD --> HAS_DOCUMENTS{"대기 문서가 있는가?"}
        HAS_DOCUMENTS -- "없음" --> EMPTY(["0건 정상 종료"])
        HAS_DOCUMENTS -- "있음" --> HAS_CANDIDATES{"종목·지표 후보가 있는가?"}
        HAS_CANDIDATES -- "없음" --> CONFIG_FAIL(["즉시 실패<br/>마스터 데이터 필요"])
        HAS_CANDIDATES -- "있음" --> MODEL["ChatOpenAI 생성<br/>gpt-5.6-luna · SDK 재시도 없음"]
    end

    subgraph BATCH["LangGraph 1 · AssessmentBatch"]
        MODEL --> FAN_OUT["Send로 문서별 fan-out"]
        FAN_OUT --> ASSESS_ONE["assess_one × N<br/>기본 최대 4건 동시 실행"]
    end

    subgraph DOCUMENT["LangGraph 2 · DocumentAssessor · 문서마다 실행"]
        ASSESS_ONE --> PROMPT["문서 + 허용 태그 후보<br/>+ global / korea / us 관점"]
        PROMPT --> CALL["call<br/>JSON Schema를 붙여 모델 호출"]
        CALL --> SCHEMA{"제공처가 Schema를 지원하는가?"}
        SCHEMA -- "아니오" --> FALLBACK["Schema 없이 즉시 재호출"]
        SCHEMA -- "예" --> PARSE["JSON 추출 · Pydantic 검증"]
        FALLBACK --> PARSE
        PARSE --> VALID{"응답이 유효한가?"}
        VALID -- "예" --> SUCCESS["평가 결과<br/>종목 · 지표 · 방향 · 0~8점 · 근거"]
        VALID -- "아니오" --> FIRST_FAILURE{"아직 교정 전인가?"}
        FIRST_FAILURE -- "예" --> REPAIR["repair<br/>형식 교정 지시 추가"]
        REPAIR --> CALL
        FIRST_FAILURE -- "아니오" --> DOCUMENT_FAIL["문서 실패 결과"]
    end

    SUCCESS --> GATHER["문서별 결과 집계"]
    DOCUMENT_FAIL --> GATHER

    subgraph STORE["DAG · 결과 저장"]
        GATHER --> RESULT{"문서 평가 성공?"}
        RESULT -- "성공" --> FILTER["마스터에 없는 태그 제거"]
        FILTER --> TRANSACTION["문서 1건당 독립 트랜잭션<br/>평가 · 종목 · 지표 저장"]
        RESULT -- "실패" --> KEEP_PENDING["assessed_at을 NULL로 유지"]
        TRANSACTION --> ALL_FAILED{"전체 문서가 실패했는가?"}
        KEEP_PENDING --> ALL_FAILED
        ALL_FAILED -- "예" --> AIRFLOW_RETRY["Airflow 태스크 재시도<br/>10분 뒤 1회"]
        ALL_FAILED -- "아니오" --> DONE(["정상 종료"])
        KEEP_PENDING -. "다음 정시 실행에서 다시 선택" .-> TIMER
    end
```

실패한 문서는 삭제하거나 별도 상태로 바꾸지 않는다. 일부 문서만 성공하면 성공 건은 커밋하고,
실패 건은 다음 정시 실행에서 다시 평가한다. 모든 문서가 실패했을 때만 Airflow 태스크가 재시도된다.

## 구현 위치

- [Airflow DAG](../../airflow/dags/document_assessment_hourly.py)
- [배치 및 문서별 LangGraph](../../airflow/modules/assessment.py)
- [LangChain 모델 생성과 호출](../../airflow/modules/llm.py)
- [구조화 응답 Schema](../../airflow/modules/schema.py)
