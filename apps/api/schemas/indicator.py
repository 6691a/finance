"""지표 시계열의 응답 계약.

**`kind`가 계약의 일부다.** 국채 곡선에 CD 91일이나 CPI가 섞이면 단위가 다른 값이 한 축에
올라가 화면이 조용히 거짓말을 한다. 그래서 목록도 관측값도 `kind`를 함께 낸다.

**`provider`도 함께 낸다.** `series_id`는 제공처 안에서만 고유해서, 그 하나로 거는 조회는
제공처가 늘어나면 조용히 틀린다.
"""

from datetime import date

from pydantic import Field

from apps.api.schemas.common import ApiModel, Page


class IndicatorSeriesItem(ApiModel):
    """지표 시계열 하나. 마스터에 **실제 쌓인 것**을 붙여 준다."""

    provider: str = Field(description="이 값을 준 제공처(fred·ecos·mof·boe·bbk·ecb).")
    series_id: str = Field(
        description=(
            "제공처 안에서 고유한 식별자. **사람이 읽을 수 있어야 한다** — "
            "숫자뿐인 제공처 코드는 수집기가 `KTB10Y` 같은 ID로 바꿔 저장한다."
        )
    )
    kind: str = Field(
        description=(
            "시계열의 종류(government_bond·money_market·price_index·activity). "
            "**조회하는 쪽은 이것을 반드시 건다** — 단위가 다른 값이 한 축에 섞이면 안 된다."
        )
    )
    country: str = Field(description="발행 국가(ISO 3166-1 alpha-2). 유로 지역처럼 국가가 아닌 통화권은 XM이다.")
    country_name: str = Field(description="국가 표시 이름.")
    label: str = Field(description="차트와 표에 쓰는 표시 이름.")
    maturity_months: int | None = Field(
        default=None,
        description=(
            "만기 개월 수(3개월=3, 10년=120). 91일물은 3이다. "
            "**만기 개념이 없는 지표(물가지수·실물활동)는 null이고 0이 아니다.**"
        ),
    )
    unit: str | None = Field(
        default=None,
        description=(
            "정규화한 단위 표기. 제공처 표기가 아니라 우리가 맞춘 값이다 — "
            "연이율 퍼센트는 제공처가 `연%`든 `Percent`다. 관측값이 없으면 null이다."
        ),
    )
    rows: int = Field(default=0, description="쌓인 관측값 수.")
    observed_from: date | None = Field(default=None, description="가장 오래된 관측일.")
    observed_to: date | None = Field(default=None, description="가장 최근 관측일. 최신성이 여기 보인다.")


IndicatorSeriesList = Page[IndicatorSeriesItem]


class IndicatorPoints(ApiModel):
    """한 시계열의 관측값. 시세와 같은 컬럼 지향이다."""

    provider: str = Field(description="요청한 제공처.")
    series_id: str = Field(description="요청한 시계열.")
    kind: str = Field(description="이 시계열의 종류. 화면이 축 단위를 여기서 정한다.")
    label: str = Field(description="표시 이름.")
    unit: str | None = Field(default=None, description="정규화한 단위 표기.")
    points: int = Field(description="배열 하나의 길이.")
    dates: tuple[date, ...] = Field(
        default=(),
        description=(
            "관측일. 오름차순이다. **기준 시간대는 제공처가 정한다** — "
            "ECOS는 KST 고시일, FRED는 미국 영업일이다."
        ),
    )
    values: tuple[float, ...] = Field(default=(), description="관측값. `unit`이 뜻을 정한다.")


class CurvePoint(ApiModel):
    """곡선의 한 점. **만기가 x축이다.**"""

    series_id: str = Field(description="이 만기의 시계열 식별자.")
    maturity_months: int = Field(description="만기 개월 수. 이 값으로 정렬한다.")
    label: str = Field(description="표시 이름.")
    observation_date: date = Field(description="이 값의 관측일. 나라마다 마지막 고시일이 다를 수 있다.")
    value: float = Field(description="그 만기의 값.")


class CurveCountry(ApiModel):
    """한 나라의 곡선."""

    provider: str = Field(description="이 곡선을 준 제공처.")
    country: str = Field(description="ISO 3166-1 alpha-2. 유로 지역은 XM이다.")
    country_name: str = Field(description="국가 표시 이름.")
    unit: str | None = Field(default=None, description="정규화한 단위 표기.")
    points: tuple[CurvePoint, ...] = Field(default=(), description="만기 오름차순.")


class CurveResponse(ApiModel):
    """국가별 국채 곡선.

    **만기 개념이 없는 계열은 아예 들어오지 않는다**(`maturity_months IS NULL`).
    `kind`도 `government_bond`로 고정이다 — 단기 자금시장 금리가 곡선에 섞이면 안 된다.
    """

    as_of: date = Field(description="이 곡선이 기준으로 삼은 날짜. 각 계열의 그 날짜 이전 마지막 값을 쓴다.")
    countries: tuple[CurveCountry, ...] = Field(default=(), description="국가 코드 순.")
