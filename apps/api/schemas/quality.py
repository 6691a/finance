"""품질 집계의 응답 계약.

**표를 둘로 나눈다.** 예측 품질의 키는 원 추론을 만든 실행의 모델·판이고, 해설 품질의
키는 사후 해설 LLM의 모델·판이다. 두 판은 서로 독립으로 움직인다 — 원 추론이 판 6에서
7로 올라가도 해설은 `2/informed` 그대로일 수 있다. 한 행에 놓으면 "판 7이 이유 지지율도
올렸다"로 읽히는데 그 손잡이는 움직인 적이 없다(`TUNING.md` 1절의 "한 번에 한 손잡이").

**종합 점수를 만들지 않는다.** Brier(방향), 크기 오차(폭), verdict(이유)는 서로 다른
것을 재고 단위도 다르다.

**표본 수는 metric마다 따로 낸다.** 결측 조건이 달라서다 — `predicted_return_pct`는
지평 0에만 있고 flat 실현이면 그마저 비어 있다. 합쳐 하나로 내면 평균 옆의 n이 거짓이 된다.
"""

from datetime import date

from pydantic import Field

from apps.api.schemas.common import ApiModel

# 균등 확률(1/3씩)의 3-class Brier. 이 값을 넘는 평균은 "찍는 것보다 나쁘다"다.
UNIFORM_BRIER = 2 / 3


class ForecastQualityRow(ApiModel):
    """예측 품질 한 행. 키의 모델·판은 **원 추론을 생성한 실행의 값**이다."""

    week_start: date = Field(description="주의 시작(월요일, KST `run_date` 기준).")
    horizon_days: int = Field(description="채점 지평(0·1·3·5). KRX 영업일 수다.")
    run_slot: str = Field(description="원 추론의 슬롯.")
    llm_model: str = Field(description="원 추론을 만든 모델.")
    prompt_version: str = Field(description="원 추론의 프롬프트 판.")
    mean_brier: float | None = Field(
        default=None,
        description="이 키의 평균 3-class Brier(0이 완벽, 2가 최악). 표본이 없으면 null이고 0.0이 아니다.",
    )
    brier_samples: int = Field(default=0, description="평균에 들어간 채점 건수.")
    beats_uniform: bool | None = Field(
        default=None,
        description=(
            f"평균 Brier가 균등확률 baseline({UNIFORM_BRIER:.3f})보다 낮은가. "
            "표본이 없으면 null이다. **정확도 등급이 아니라 그 한 줄 비교다.**"
        ),
    )
    mean_return_error_pct: float | None = Field(
        default=None,
        description=(
            "부호를 유지한 평균 크기 오차(퍼센트포인트). 양수면 과소추정, 음수면 과대추정이다. "
            "**Brier와 합치지 않는다.**"
        ),
    )
    mae_return_pct: float | None = Field(
        default=None, description="크기 오차의 절대값 평균. 부호가 상쇄되지 않는 쪽이다."
    )
    return_samples: int = Field(
        default=0,
        description=(
            "크기 오차의 표본 수. **Brier 표본과 다르다** — 크기 비교는 지평 0의 방향 적중에만 붙는다."
        ),
    )
    mean_tool_calls: float | None = Field(
        default=None, description="succeeded 실행 하나당 평균 툴 호출 수. 같은 실행을 두 번 세지 않는다."
    )
    mean_tool_result_chars: float | None = Field(
        default=None, description="succeeded 실행 하나당 모델에게 전달된 결과 문자 수의 평균."
    )
    run_samples: int = Field(
        default=0,
        description=(
            "툴 평균에 들어간 **서로 다른 실행 수**. 한 실행이 여러 추론을 만들어도 한 번만 센다."
        ),
    )


class NarrativeQualityRow(ApiModel):
    """해설 품질 한 행. 키의 모델·판은 **사후 해설 실행의 값**이다.

    `run_slot`이 키에 없는 것은 해설이 슬롯이 아니라 지평으로 갈리기 때문이다.
    """

    week_start: date = Field(description="주의 시작(월요일, 원 추론 `run_date` 기준).")
    horizon_days: int = Field(description="해설 지평(1·3·5). 지평 0은 해설을 받지 않는다.")
    llm_model: str = Field(description="해설을 만든 모델. 원 추론의 모델과 다를 수 있다.")
    prompt_version: str = Field(description="해설 프롬프트 판과 변형(`<판>/<변형>`).")
    supported: int = Field(default=0, description="이후 보도가 원 추론의 이유를 지지한 건수.")
    contradicted: int = Field(default=0, description="반박한 건수.")
    unresolved: int = Field(default=0, description="가릴 수 없었던 건수.")
    verdict_samples: int = Field(default=0, description="판정이 붙은 전체 건수. 셋의 합이다.")


class QualityResponse(ApiModel):
    """품질 화면 한 번의 응답. **배열 둘을 한 응답에 담고 표는 둘로 그린다.**

    라우트를 가르지 않는 이유는 화면이 늘 둘을 함께 그리기 때문이고, 배열을 가르는
    이유는 키가 서로 다른 판이기 때문이다.
    """

    forecast: tuple[ForecastQualityRow, ...] = Field(
        default=(), description="예측 품질. week_start·horizon_days 순이다."
    )
    narrative: tuple[NarrativeQualityRow, ...] = Field(
        default=(), description="해설 품질. week_start·horizon_days 순이다."
    )
    uniform_brier: float = Field(
        default=UNIFORM_BRIER,
        description="균등 확률 baseline. 화면이 같은 숫자를 다시 적지 않게 응답이 들고 있는다.",
    )
