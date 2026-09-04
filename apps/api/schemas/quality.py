"""품질 집계의 응답 계약.

**표를 둘로 나눈다.** 전망 품질의 키는 그 전망을 만든 모델·판이고, 관찰 품질의 키는
장후 관찰 실행의 모델·판이다. 두 판은 서로 독립으로 움직인다(`kospi-forecast.md` §1의
"판 동결 20영업일"이 전망 쪽에만 걸린다). 한 행에 놓으면 전망 판을 올린 효과가 관찰
쪽 변화로 읽힌다.

**종합 점수를 만들지 않는다.** 방향 적중, 밴드 적중, 크기 오차는 서로 다른 것을 재고
단위도 다르다.

**표본 수를 metric마다 함께 낸다.** 채점 0건의 비율은 `null`이지 0.0이 아니다 — 0.0으로
두면 "다 틀렸다"로 읽힌다.
"""

from datetime import date

from pydantic import Field

from apps.api.schemas.common import ApiModel

# 방향이 둘(up·down)뿐이라 찍기의 기대 적중률이 0.5다. 이 값을 못 넘는 판은 뜻이 없다.
COIN_FLIP_HIT_RATE = 0.5


class ForecastQualityRow(ApiModel):
    """전망 품질 한 행. 키는 **주 · 슬롯 · 모델 · 판**이다."""

    week_start: date = Field(description="주의 시작(월요일, KST `run_date` 기준).")
    slot: str = Field(description="pre_open·midday·pre_close.")
    llm_model: str = Field(description="그 전망을 만든 모델.")
    prompt_version: str = Field(description="그 전망의 프롬프트 판.")
    graded: int = Field(default=0, description="채점된 건수. **모든 비율의 분모다.**")
    pending: int = Field(default=0, description="아직 채점되지 않은 건수.")
    hits: int = Field(default=0, description="방향을 맞힌 건수.")
    hit_rate: float | None = Field(
        default=None, description="방향 적중률(0~1). 채점 0건이면 null이고 0.0이 아니다."
    )
    beats_coin_flip: bool | None = Field(
        default=None,
        description=(
            f"적중률이 찍기({COIN_FLIP_HIT_RATE})보다 높은가. 표본이 없으면 null이다. "
            "**등급이 아니라 그 한 줄 비교다.**"
        ),
    )
    within_band: int = Field(default=0, description="|실제 − 기대| ≤ 폭 인 건수.")
    band_rate: float | None = Field(default=None, description="밴드 적중률(0~1).")
    mean_abs_error: float | None = Field(
        default=None, description="|실제 − 기대|의 평균(%p). 폭이 적정한지를 이것과 비교한다."
    )
    mean_band_pct: float | None = Field(
        default=None,
        description=(
            "부른 폭의 평균(%p). **`mean_abs_error`보다 작으면 구조적으로 못 맞히는 폭이다** — "
            "판 3이 그 자리를 고쳤다."
        ),
    )
    mean_expected_pct: float | None = Field(
        default=None, description="부른 기대 등락률의 평균(%). 한쪽으로 치우쳤는지가 보인다."
    )
    weak: int = Field(default=0, description="이유 0건으로 저장된 약한 답의 수.")
    rejected_reasons: int = Field(default=0, description="검증이 버린 이유의 합.")


class ReviewQualityRow(ApiModel):
    """관찰 품질 한 행. 키는 **주 · 모델 · 판**이다. 슬롯이 없다 — 관찰은 하루에 한 번이다."""

    week_start: date = Field(description="주의 시작(월요일, KST `run_date` 기준).")
    llm_model: str = Field(description="관찰을 만든 모델.")
    prompt_version: str = Field(description="관찰 프롬프트 판. 전망 판과 따로 오른다.")
    runs: int = Field(default=0, description="성공한 관찰 실행 수. 모든 평균의 분모다.")
    observations_written: int = Field(default=0, description="그래프에 쓴 관측의 합.")
    mean_observations: float | None = Field(
        default=None,
        description=(
            "실행 하나당 관측 수. **낮으면 조용한 날이 많거나 관찰이 게으른 것이다** — "
            "둘을 이 값 하나로는 못 가른다."
        ),
    )
    memories_written: int = Field(default=0, description="새로 쓴 메모의 합.")
    memories_rejected: int = Field(
        default=0, description="상한·중복으로 버린 합. **0이 아니면 상한을 치고 있다.**"
    )
    memories_dropped: int = Field(default=0, description="모델이 내린 합.")
    memories_expired: int = Field(default=0, description="나이 상한으로 코드가 내린 합.")
    rejected: int = Field(
        default=0, description="검증이 버린 관찰의 합. **0이 아니면 조회 안 한 요인을 인용했다.**"
    )
    mean_tool_calls: float | None = Field(default=None, description="실행 하나당 평균 툴 호출 수.")
    truncated: int = Field(default=0, description="툴 상한에서 끊긴 실행 수.")


class QualityResponse(ApiModel):
    """품질 화면 한 번의 응답. **배열 둘을 한 응답에 담고 표는 둘로 그린다.**

    라우트를 가르지 않는 이유는 화면이 늘 둘을 함께 그리기 때문이고, 배열을 가르는
    이유는 키가 서로 다른 판이기 때문이다.
    """

    forecast: tuple[ForecastQualityRow, ...] = Field(
        default=(), description="전망 품질. week_start·slot 순이다."
    )
    review: tuple[ReviewQualityRow, ...] = Field(
        default=(), description="관찰 품질. week_start 순이다."
    )
    coin_flip_hit_rate: float = Field(
        default=COIN_FLIP_HIT_RATE,
        description="찍기의 기대 적중률. 화면이 같은 숫자를 다시 적지 않게 응답이 들고 있는다.",
    )
