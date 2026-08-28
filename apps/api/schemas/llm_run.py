"""LLM 실행 원장의 응답 계약.

**조회 단위가 추론이 아니라 대화다.** 대화 하나가 여러 추론을 만들고 실패 대화에는
추론이 없다 — 그래서 툴 호출 배열은 `apps/api/schemas/thesis.py`가 아니라 여기 있다.

**목록의 `tool_call_count`와 상세의 `tool_calls`는 다른 칸이다.** 앞은 건수, 뒤는 배열이다.
한 이름을 목록에서는 정수로 상세에서는 배열로 내면 프런트의 `LlmRunDetail extends
LlmRunItem`이 그 자리에서 깨진다.

**결과 전문(`result`)은 단건 응답에만 있다.** 상세의 목록에 실으면 왕복 하나가 수 MB가
된다 — 툴 하나를 골랐을 때만 가져온다.
"""

from datetime import date
from typing import Any

from pydantic import Field

from apps.api.schemas.common import ApiModel, Page, UtcDatetime


class ToolCallSummary(ApiModel):
    """대화 안의 툴 호출 하나. **결과 전문은 없다.**"""

    seq: int = Field(
        description=(
            "대화 안의 기록 순서(1부터). **인과 순서가 아니다** — 같은 round_no의 호출은 "
            "한 모델 응답의 sibling이라 병렬일 수 있다."
        )
    )
    round_no: int = Field(description="몇 번째 tool round의 요청인가(1부터). 한 라운드가 모델 응답 하나다.")
    tool_call_id: str = Field(description="제공처가 준 tool call id. 요청과 결과를 잇는 키다.")
    tool_name: str = Field(description="부른 툴 이름.")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="모델이 보낸 인자 원본(StructuredTool 검증 전)."
    )
    validated_arguments: dict[str, Any] | None = Field(
        default=None,
        description=(
            "검증·기본값 적용 뒤 실제 함수에 들어간 인자. **unknown tool과 인자 검증 실패는 "
            "함수에 진입하지 않아 null이다.**"
        ),
    )
    requested_at: UtcDatetime = Field(description="모델의 호출 요청을 등록한 시각(UTC).")
    duration_ms: int | None = Field(
        default=None, description="실제 함수가 돈 시간(밀리초). 진입 전 거절은 null이다."
    )
    result_chars: int = Field(default=0, description="결과 문자 수. 오류면 0이다.")
    delivered: bool = Field(
        description=(
            "이 결과·오류가 모델 대화에 실제로 돌아갔나. **false는 실행됐지만 모델이 못 본 "
            "것이다** — 모델이 본 입력으로 읽으면 안 된다."
        )
    )
    error_kind: str | None = Field(
        default=None,
        description="실패 종류(unknown_tool·validation·limit·execution·cancelled). 오류가 있을 때만 있다.",
    )
    error: str | None = Field(default=None, description="실패 사유. `result`와 동시에 채워지지 않는다.")
    url: str = Field(description="결과 전문을 가진 단건 조회 경로.")


class ToolCallDetail(ToolCallSummary):
    """툴 호출 하나의 전문. **`result`와 `error`는 배타다**(DB CHECK가 강제한다)."""

    result: str | None = Field(
        default=None,
        description=(
            "결과 본문 전문. 성공이면 툴이 돌려준 JSON 문자열이고 실패면 null이다. "
            "**JSON이 아닐 수 있다** — 파싱 실패는 화면 오류가 아니라 평문 표시다."
        ),
    )


class ProducedThesis(ApiModel):
    """이 대화가 만든 추론 하나. 목록 화면의 링크가 이것을 쓴다."""

    id: int = Field(description="추론 id.")
    run_date: date = Field(description="대상 세션 날짜(KST).")
    run_slot: str = Field(description="대상 슬롯.")
    subject_kind: str = Field(description="대상 종류.")
    subject_code: str = Field(description="대상 식별자.")
    label: str = Field(description="그 시점의 표시 이름.")
    url: str = Field(description="추론 상세 경로.")


class NarratedOutcome(ApiModel):
    """이 대화가 해설한 지평 하나."""

    thesis_id: int = Field(description="해설이 붙은 원 추론의 id.")
    horizon_days: int = Field(description="해설한 지평(1·3·5). KRX 영업일 수다.")
    subject_code: str = Field(description="원 추론의 대상 식별자.")
    label: str = Field(description="원 추론 시점의 표시 이름.")
    verdict: str | None = Field(
        default=None, description="원 추론의 이유가 이후 보도로 지지됐나(supported/contradicted/unresolved)."
    )
    url: str = Field(description="원 추론 상세 경로.")


class LlmRunItem(ApiModel):
    """실행 목록 한 줄."""

    id: int = Field(description="대화 레코드 id.")
    kind: str = Field(
        description=(
            "대화의 종류. forecast·review·nxt_review는 추론 생성, narration은 사후 해설. "
            "**슬롯에서 유도할 수 없다** — 같은 post_close에 둘 다 있다."
        )
    )
    run_date: date = Field(
        description=(
            "대화가 대상으로 삼은 세션 날짜(KST). **해설이면 원 추론일이다** — "
            "실행일이 아니므로 목록 필터의 축과 다르다."
        )
    )
    run_slot: str | None = Field(
        default=None,
        description=(
            "대상 슬롯. 해설이면 원 추론의 슬롯이다. **인과 그래프(`causal`) 실행은 null이다** — "
            "그 대화의 축은 슬롯이 아니라 주(week)다."
        ),
    )
    horizon_days: int | None = Field(
        default=None, description="해설 대화의 지평(1·3·5). 생성 대화는 null이다."
    )
    as_of_at: UtcDatetime = Field(description="이 대화의 툴 조회 기준 시각(UTC). event-time cutoff다.")
    dag_run_id: str = Field(description="이 대화를 돌린 Airflow dag_run_id.")
    try_number: int = Field(description="그 태스크의 시도 번호(1부터). 재시도는 새 대화다.")
    llm_model: str = Field(description="이 대화를 돈 모델 식별자.")
    prompt_version: str = Field(description="프롬프트 판. 해설은 `<판>/<변형>` 형태다.")
    started_at: UtcDatetime = Field(description="대화를 시작한 시각(UTC). **목록 필터와 정렬의 축이다.**")
    finished_at: UtcDatetime | None = Field(
        default=None, description="끝난 시각(UTC). null이면 종료를 기록하지 못했다는 뜻이다."
    )
    duration_ms: int | None = Field(
        default=None,
        description="전체 소요(밀리초). **종료를 기록하지 못한 실행은 null이다** — 0으로 오지 않는다.",
    )
    status: str = Field(
        description=(
            "running·succeeded·failed. **running은 \"시작했지만 종료를 기록하지 못했다\"이기도 "
            "하다** — 지금 도는 중인지 끊긴 것인지 이 값만으로 가르지 않는다."
        )
    )
    error: str | None = Field(default=None, description="실패 사유. status가 failed일 때만 있다.")
    tool_rounds: int = Field(description="조사 왕복 수. 왕복 하나가 모델 호출 하나다.")
    tool_call_count: int = Field(
        description=(
            "기록된 툴 호출 수. **상한을 재는 카운터와 다른 수다** — 모르는 툴과 인자 검증 "
            "실패도 세지만 툴박스의 예산 카운터는 함수에 진입한 것만 센다."
        )
    )
    tool_result_chars: int = Field(
        description="모델에게 실제로 돌아간 결과의 누적 문자 수(delivered=true만)."
    )
    investigation_truncated: bool = Field(
        default=False,
        description=(
            "모델이 툴을 더 부르겠다고 했는데 상한에서 끊긴 실행인가. 스스로 끝낸 실행과 "
            "`tool_rounds` 하나로는 구분되지 않는다."
        ),
    )
    produced_count: int = Field(
        default=0,
        description=(
            "이 대화가 만든 산출물 수. 생성 대화는 추론 수, 해설 대화는 해설한 지평 수다. "
            "**실패·running 실행은 0이다.**"
        ),
    )
    subjects_requested: int | None = Field(
        default=None,
        description="이 대화에 요청한 대상 수. 한 대화가 대상 여럿을 다루는 흐름에만 있다.",
    )
    subjects_answered: int | None = Field(
        default=None,
        description=(
            "그중 모델이 실제로 답한 수. **요청보다 적으면 조용히 빠진 대상이 있다는 뜻이다.**"
        ),
    )
    prompt_tokens: int | None = Field(default=None, description="입력 토큰. 캐시분을 포함한다.")
    cached_prompt_tokens: int | None = Field(
        default=None,
        description=(
            "그중 프롬프트 캐시에서 읽은 입력 토큰. **`prompt_tokens`에 포함된다** — "
            "제공처가 이 부분을 훨씬 싸게 청구하므로 이 칸이 없으면 실제 비용을 알 수 없다."
        ),
    )
    completion_tokens: int | None = Field(
        default=None, description="출력 토큰. **`reasoning_tokens`를 포함한다.**"
    )
    reasoning_tokens: int | None = Field(
        default=None, description="그중 사고 토큰. 제공처가 안 주면 null이다."
    )
    url: str = Field(description="실행 상세 경로.")


LlmRunList = Page[LlmRunItem]


class LlmRunDetail(LlmRunItem):
    """실행 상세. 목록 한 줄에 툴 호출과 산출물을 더한 것이다."""

    tool_calls: tuple[ToolCallSummary, ...] = Field(
        default=(),
        description=(
            "기록된 툴 호출. `seq` 오름차순이고 **결과 전문은 없다** — 단건 조회가 준다. "
            "실패 전까지의 기록이라 실패 실행에도 값이 있다."
        ),
    )
    produced_theses: tuple[ProducedThesis, ...] = Field(
        default=(), description="이 대화가 만든 추론. 해설·실패·running 실행은 빈 배열이다."
    )
    narrated_outcomes: tuple[NarratedOutcome, ...] = Field(
        default=(), description="이 대화가 해설한 지평. 생성·실패·running 실행은 빈 배열이다."
    )
