"""추론 목록과 상세의 응답 계약.

**필드마다 `description`을 단다.** 진짜 화면이 오기 전까지 `/docs`가 유일한 UI이고,
읽는 사람이 "이 칸이 언제 비나"를 코드를 열지 않고 알 수 있어야 한다.
"""

from datetime import date
from typing import Any

from pydantic import Field

from apps.api.schemas.common import ApiModel, UtcDatetime


class ThesisSummary(ApiModel):
    """목록 한 줄. **이유와 `input_state`는 없다** — 100건이면 응답이 수백 KB가 된다."""

    id: int = Field(description="추론 레코드 id. 상세·그래프 라우트의 경로 인자다.")
    run_date: date = Field(description="추론이 대상으로 삼은 세션 날짜(KST). 시각은 담지 않는다.")
    run_slot: str = Field(
        description=(
            "만든 슬롯. pre_open(장전 전망), intraday_morning·intraday_midday·"
            "intraday_afternoon·pre_close(장중 전망), post_close(장후 리뷰), "
            "post_nxt_close(NXT 애프터마켓 리뷰). **슬롯이 곧 추론의 종류다.**"
        )
    )
    as_of_at: UtcDatetime = Field(
        description=(
            "관측 상태와 툴 조회의 기준 시각(UTC). 벽시계가 아니라 슬롯이 정한다. "
            "**정렬 키가 이것이다** — run_slot은 문자열이라 정렬하면 시간이 뒤집힌다."
        )
    )
    subject_kind: str = Field(description="대상 종류. index 또는 stock.")
    subject_code: str = Field(description="대상 식별자. 지수는 KOSPI·KOSDAQ, 종목은 6자리 코드.")
    label: str = Field(description="추론 시점의 표시 이름 스냅샷. 마스터에서 이름이 바뀌어도 당시 표기가 남는다.")
    prob_up: float = Field(description="상승 확률 0~1. 셋의 합은 1이다.")
    prob_down: float = Field(description="하락 확률 0~1. 셋의 합은 1이다.")
    prob_flat: float = Field(
        description=(
            "횡보 확률 0~1. **채점 창의 등락률이 ±0.3% 안에 들어올 확률**이지 "
            "\"방향을 모르겠다\"가 아니다."
        )
    )
    up_return_pct: float | None = Field(
        default=None,
        description=(
            "**상승한다는 조건에서의** 등락률(퍼센트, 양수). 확률을 곱한 기대값이 아니다. "
            "프롬프트 판 7 이전 추론과 모델이 규칙을 어긴 값은 null이다."
        ),
    )
    down_return_pct: float | None = Field(
        default=None,
        description="**하락한다는 조건에서의** 등락률(퍼센트, 양수 크기). 위와 같은 규칙이다.",
    )
    graded_horizons: int = Field(
        default=0,
        description="채점이 끝난 지평 수(0~4). 채점은 예측 슬롯에만 붙는다 — 리뷰 둘은 늘 0이다.",
    )
    narrated_horizons: int = Field(
        default=0, description="사후 해설이 붙은 지평 수(0~3). 지평 0은 해설을 받지 않는다."
    )
    mean_brier: float | None = Field(
        default=None,
        description=(
            "채점된 지평의 평균 Brier(0이 완벽, 2가 최악). 균등 확률 baseline이 약 0.667이다. "
            "채점이 없으면 null이다 — **0.0으로 오지 않는다.**"
        ),
    )


class ThesisList(ApiModel):
    """목록 한 쪽."""

    items: tuple[ThesisSummary, ...] = Field(default=(), description="as_of_at 내림차순.")
    limit: int = Field(description="요청한 쪽 크기.")
    offset: int = Field(description="건너뛴 건수.")
    has_more: bool = Field(
        description=(
            "다음 쪽이 있나. `limit + 1`건을 읽어 판단한다 — **총 건수는 세지 않는다.** "
            "실질 페이지네이션은 날짜 구간을 좁히는 것이다."
        )
    )


class EvidenceCitation(ApiModel):
    """모델이 인용한 근거 하나. **DB에 있는 칸을 다 낸다.**"""

    rank: int = Field(description="모델이 인용한 순서(1부터). Slack은 상위 셋만 보인다.")
    kind: str = Field(
        description=(
            "출처 종류. document(기사·리포트), disclosure(DART 공시), "
            "macro_change(매크로 변화), technical_signal(기술적 매매 신호)."
        )
    )
    ref: str = Field(
        description=(
            "`<kind>:<id>` 2단 식별자(document:4471, disclosure:20260821000123). "
            "**그래프 응답의 Evidence 노드 id가 이 값 그대로다.**"
        )
    )
    title: str = Field(description="인용 시점의 제목 스냅샷. 원본이 지워지거나 바뀌어도 무엇을 인용했는지 읽힌다.")
    url: str | None = Field(
        default=None,
        description=(
            "**document와 disclosure에만 붙는다.** macro_change와 technical_signal은 링크할 곳이 "
            "없어 항상 null이다 — 결함이 아니므로 kind로 가려 읽는다."
        ),
    )
    direction: str | None = Field(
        default=None,
        description=(
            "이 근거가 대상을 미는 방향(up/down/flat). **원 추론의 인용에만 있고** "
            "지평별 사후 해설의 인용은 null이다."
        ),
    )
    mechanism: str | None = Field(
        default=None, description="그 방향으로 작용하는 경로 한 문장. direction과 함께 채워지거나 함께 빈다."
    )
    detail: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "툴이 준 수치 스냅샷(등락률, 가치 점수 등). 근거가 **당시** 어떤 값이었는지다 — "
            "모양은 kind마다 다르다."
        ),
    )


class PrecedentRef(ApiModel):
    """프롬프트에서 본 과거 추론 하나. "이 판단이 어느 과거 판단을 알고 내려졌나"다."""

    id: int = Field(description="과거 추론의 id. 그 상세로 바로 갈 수 있다.")
    run_date: date = Field(description="그 추론의 세션 날짜(KST).")
    run_slot: str = Field(description="그 추론의 슬롯.")
    subject_kind: str = Field(description="대상 종류.")
    subject_code: str = Field(description="대상 식별자. 같은 대상의 과거 추론만 프롬프트에 실린다.")
    label: str = Field(description="그 시점의 표시 이름.")
    prob_up: float = Field(description="그 추론의 상승 확률.")
    prob_down: float = Field(description="그 추론의 하락 확률.")
    prob_flat: float = Field(description="그 추론의 횡보 확률.")


class LlmRunSummary(ApiModel):
    """이 판단을 만든 LLM 대화. **툴 호출 배열은 여기 없다.**

    대화 하나가 여러 추론을 만들고 실패 대화에는 추론이 없다. 같은 배열을 모든 상세에
    복제하지 않고 id만 잇는다.
    """

    id: int = Field(description="대화 레코드 id.")
    kind: str = Field(
        description="대화의 종류. forecast·review·nxt_review는 추론 생성, narration은 사후 해설."
    )
    status: str = Field(
        description=(
            "running·succeeded·failed. **running은 "
            "\"시작했지만 종료를 기록하지 못했다\"이기도 하다** — 프로세스가 죽으면 그렇게 남는다."
        )
    )
    llm_model: str = Field(description="이 대화를 돈 모델 식별자.")
    prompt_version: str = Field(
        description="프롬프트 판. 해설은 `<판>/<변형>` 형태라 생성 대화와 번호 체계가 다르다."
    )
    try_number: int = Field(
        description="Airflow 태스크의 시도 번호(1부터). 재시도는 새 대화라 이 값으로 구분한다."
    )
    started_at: UtcDatetime = Field(description="대화를 시작한 시각(UTC).")
    finished_at: UtcDatetime | None = Field(
        default=None, description="끝난 시각(UTC). null이면 종료를 기록하지 못했다는 뜻이다."
    )
    tool_rounds: int = Field(description="조사 왕복 수. 왕복 하나가 모델 호출 하나다.")
    tool_calls: int = Field(
        description=(
            "기록된 툴 호출 수. **상한을 재는 카운터와 다른 수다** — 모르는 툴과 인자 검증 "
            "실패도 세지만 툴박스의 예산 카운터는 함수에 진입한 것만 센다."
        )
    )
    tool_result_chars: int = Field(
        description=(
            "모델에게 실제로 돌아간 결과의 누적 문자 수. **예산 카운터와 다른 수다** — "
            "그쪽은 버려진 결과도 센다."
        )
    )
    error: str | None = Field(default=None, description="실패 사유. status가 failed일 때만 있다.")


class ThesisOutcomeItem(ApiModel):
    """한 지평의 채점과 해설. **채우는 주체가 다르다** — 채점은 순수 함수, 해설은 LLM이다.

    둘 중 하나만 있는 행이 정상이라 null 조합을 그대로 낸다.
    """

    horizon_days: int = Field(
        description="지평 길이. **KRX 영업일 수이고 달력일이 아니다.** 0·1·3·5 넷이다."
    )
    as_of_at: UtcDatetime = Field(description="이 지평의 기준 시각(UTC). 그 영업일 장후다.")
    evaluated_at: UtcDatetime | None = Field(
        default=None, description="채점한 시각(UTC). null은 미채점이다 — 채점은 예측 슬롯에만 붙는다."
    )
    actual_return_pct: float | None = Field(
        default=None,
        description=(
            "예측 시점 기준가 대비 이 지평 종가의 누적 등락률(퍼센트). "
            "**기준가는 지평이 달라도 같다** — 그래야 T+1과 T+5를 비교할 수 있다."
        ),
    )
    actual_outcome: str | None = Field(
        default=None,
        description="누적 등락률의 분류(up/down/flat). **임계는 지평마다 다르다**(0·1은 0.3%, 3은 0.5%, 5는 0.7%).",
    )
    brier_score: float | None = Field(
        default=None,
        description="세 확률을 이 지평 결과로 매긴 3-class Brier. 0이 완벽, 2가 최악, 균등확률이 약 0.667이다.",
    )
    predicted_return_pct: float | None = Field(
        default=None,
        description=(
            "실현된 방향에 대응하는 조건부 크기 스냅샷. **지평 0에만 있고** flat 실현이거나 "
            "판 7 이전 추론이면 null이다."
        ),
    )
    return_error_pct: float | None = Field(
        default=None,
        description=(
            "`abs(actual_return_pct) - predicted_return_pct`(퍼센트포인트). **부호를 유지한다** — "
            "양수면 과소추정, 음수면 과대추정이다. Brier와 합치지 않는다."
        ),
    )
    narrative: str | None = Field(
        default=None, description="이 지평에서 쌓인 보도를 근거로 쓴 사후 해설(한국어)."
    )
    verdict: str | None = Field(
        default=None,
        description=(
            "원 추론의 **이유**가 이후 보도로 지지됐나(supported/contradicted/unresolved). "
            "**brier_score와 다른 것을 잰다** — 저쪽은 방향, 이쪽은 이유다."
        ),
    )
    narrative_at: UtcDatetime | None = Field(default=None, description="해설을 쓴 시각(UTC).")
    llm_model: str | None = Field(default=None, description="해설을 만든 모델. 원 추론의 모델과 다를 수 있다.")
    prompt_version: str | None = Field(
        default=None, description="해설 프롬프트 판과 변형(`<판>/<변형>`, 예: 2/informed)."
    )
    narration_run: LlmRunSummary | None = Field(
        default=None, description="이 해설을 만든 대화. 채점은 순수 함수라 대화가 없다."
    )
    evidence: tuple[EvidenceCitation, ...] = Field(
        default=(),
        description="**그 지평의 해설이** 인용한 근거. 원 추론의 인용과 섞지 않는다.",
    )


class ThesisDetail(ThesisSummary):
    """상세 하나. 목록 항목에 이유·관측 상태·근거·평가·과거 추론·대화를 더한 것이다."""

    up_reasoning: str = Field(description="오를 이유(한국어). 저장 전에 500자로 자른다.")
    down_reasoning: str = Field(description="내릴 이유(한국어).")
    flat_reasoning: str = Field(
        description="횡보할 이유(한국어). **세 방향을 다 쓴다** — 왜 반대를 배제했는지가 기록의 절반이다."
    )
    input_state: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "프롬프트에 준 관측 상태 스냅샷. 모델이 무엇을 보고 추론했는지의 절반이다"
            "(나머지 절반은 evidence). 모양은 슬롯마다 다르다."
        ),
    )
    tool_rounds: int = Field(description="조사 단계에서 툴을 몇 왕복 불렀는지.")
    llm_model: str = Field(description="이 추론을 만든 모델 식별자.")
    prompt_version: str = Field(description="프롬프트 판. 판을 바꾼 뒤 채점 결과를 가르는 기준이다.")
    dag_run_id: str = Field(description="이 행을 쓴 Airflow dag_run_id.")
    llm_run: LlmRunSummary | None = Field(
        default=None,
        description="이 추론을 만든 대화. **원장이 생기기 전 추론은 null이다.**",
    )
    evidence: tuple[EvidenceCitation, ...] = Field(
        default=(),
        description="**원 추론이** 인용한 근거만. rank 순이고 사후 해설의 인용은 outcomes 안에 있다.",
    )
    outcomes: tuple[ThesisOutcomeItem, ...] = Field(
        default=(), description="지평별 채점과 해설. 지평 오름차순이다."
    )
    precedents: tuple[PrecedentRef, ...] = Field(
        default=(),
        description=(
            "프롬프트에서 본 과거 추론. **나가는 방향만이다** — "
            "\"누가 나를 참고했나\"는 그래프 라우트가 답한다."
        ),
    )
