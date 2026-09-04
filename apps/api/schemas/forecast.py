"""코스피 일일 전망의 응답 계약.

**조회 단위가 슬롯이다.** 하루에 셋(장전·장중·마감전)이고 자연키가 `(run_date, slot)`이라
상세 경로도 그 둘이다 — `id`를 쓰면 같은 슬롯을 두 번 본 사람이 두 주소를 갖는다.

**목록에 `reasons`와 `input_state`를 싣지 않는다.** 한 건이 수 KB이고, 문서 목록에서
`select *`가 55KB 응답에 6.8MB를 옮긴 적이 있다(20단계 §8.6.2). 목록은 건수만 준다.

설계는 `docs/analysis/kospi-forecast-web.md` §1.1이다.
"""

from datetime import date

from pydantic import Field

from apps.api.schemas.common import ApiModel, Page, UtcDatetime


class ForecastReason(ApiModel):
    """이유 하나. **배열 순서가 곧 중요도다** — 별도 점수 칸은 없다.

    셋 중 하나로 근거를 되짚는다. 셋 다 `null`인 이유는 관측 상태에서 직접 읽은 것이다.
    """

    factor: str | None = Field(default=None, description="인용한 요인 코드. 관계 화면으로 링크한다.")
    memory_id: int | None = Field(default=None, description="인용한 메모 id. 메모 화면으로 링크한다.")
    slot_ref: str | None = Field(
        default=None, description="인용한 같은 날 앞 슬롯. `pre_open`·`midday`다."
    )
    direction: str | None = Field(default=None, description="이 이유가 가리키는 방향(up·down).")
    statement: str = Field(default="", description="이유 문장. 200자 이내다.")


class ForecastItem(ApiModel):
    """전망 목록 한 줄."""

    run_date: date = Field(description="전망이 대상으로 삼은 세션 날짜(KST).")
    slot: str = Field(
        description=(
            "pre_open·midday·pre_close. **슬롯이 기준가의 뜻을 정한다** — 장전은 전일 종가, "
            "장중 둘은 그 시각 현재가가 분모다."
        )
    )
    as_of_at: UtcDatetime = Field(description="조회 기준 시각(UTC). 벽시계가 아니라 이 값이 축이다.")
    base_price: float = Field(description="등락률의 분모.")
    base_at: UtcDatetime = Field(description="그 기준가의 시각(UTC).")
    so_far_pct: float | None = Field(
        default=None,
        description="전일 종가 대비 현재가 등락률. **장전은 null이다** — 아직 안 열렸다.",
    )
    direction: str = Field(description="up·down. **flat이 없다.**")
    expected_change_pct: float = Field(description="기준가 대비 부호 있는 기대 등락률(%).")
    band_pct: float = Field(description="± 폭(%p).")
    reason_count: int = Field(default=0, description="저장된 이유 수. 본문은 상세가 준다.")
    weak: bool = Field(
        default=False,
        description="이유가 0건으로 저장된 약한 답인가. 화면이 머리표를 붙여야 한다.",
    )
    rejected_reasons: int = Field(default=0, description="검증이 버린 이유 수.")
    actual_change_pct: float | None = Field(
        default=None, description="채점된 실제 등락률(%). 채점 전은 null이다."
    )
    hit: bool | None = Field(default=None, description="방향 적중. 채점 전은 null이다.")
    within_band: bool | None = Field(default=None, description="밴드 안. 채점 전은 null이다.")
    graded_at: UtcDatetime | None = Field(default=None, description="채점 시각(UTC).")
    prompt_version: str = Field(description="프롬프트 판.")
    llm_model: str = Field(description="부른 모델.")
    llm_run_id: int | None = Field(default=None, description="이 전망을 만든 대화 id.")
    llm_run_url: str | None = Field(default=None, description="그 대화의 상세 경로.")
    url: str = Field(description="이 전망의 상세 경로.")


ForecastList = Page[ForecastItem]


class ForecastDetail(ForecastItem):
    """전망 하나의 전부. 목록 한 줄에 이유와 관측 상태를 더한 것이다."""

    reasons: tuple[ForecastReason, ...] = Field(
        default=(), description="저장된 순서 그대로. **그 순서가 중요도다.**"
    )
    input_state: dict = Field(
        default_factory=dict,
        description=(
            "모델이 본 관측 상태 전부. **관계와 메모는 그래프가 원본이라 다음 날 바뀐다** — "
            "이 칸이 없으면 그 전망이 무엇을 보고 나왔는지 되짚을 수 없다. 모양은 프롬프트 "
            "판마다 바뀌므로 화면은 표로 그리지 않고 그대로 보인다."
        ),
    )
    dag_run_id: str = Field(default="", description="이 행을 쓴 Airflow dag_run_id.")


class ForecastAccuracyRow(ApiModel):
    """슬롯 하나의 채점 집계."""

    slot: str = Field(description="pre_open·midday·pre_close. `all`이면 슬롯 합계다.")
    graded: int = Field(default=0, description="채점된 건수. **비율의 분모이고 반드시 함께 보인다.**")
    hits: int = Field(default=0, description="방향을 맞힌 건수.")
    within_band: int = Field(default=0, description="밴드 안에 든 건수.")
    hit_rate: float | None = Field(
        default=None, description="방향 적중률(0~1). 채점 0건이면 null이다 — 0.0이 아니다."
    )
    band_rate: float | None = Field(default=None, description="밴드 적중률(0~1). 채점 0건이면 null이다.")
    mean_abs_error: float | None = Field(
        default=None, description="|실제 − 기대|의 평균(%p). 채점 0건이면 null이다."
    )
    pending: int = Field(default=0, description="아직 채점되지 않은 건수.")


class ForecastAccuracy(ApiModel):
    """채점 집계 응답. **표본 수를 반드시 함께 준다** — 3건에서 나온 67%가 100건처럼 읽히면 안 된다."""

    since: date = Field(description="집계 구간의 시작(KST, 포함).")
    until: date = Field(description="집계 구간의 끝(KST, 포함).")
    rows: tuple[ForecastAccuracyRow, ...] = Field(
        default=(), description="슬롯별 한 줄과 마지막에 `all` 한 줄."
    )
