"""모든 응답 모델이 공유하는 형태.

## 숫자와 시각

- **확률·등락률·점수는 JSON number다.** `Decimal`을 그대로 두면 Pydantic이 문자열로
  직렬화해 클라이언트가 매번 파싱한다. `docs/analysis/market-thesis/4-graph.md`도 Neo4j로
  보낼 때 `float`로 바꾸기로 이미 정했고, 유효자리가 `Numeric(5,4)`·`Numeric(8,4)`라
  왕복이 안전하다. **네 응답에 같은 규칙을 쓴다** — 그래프만 `float`, 나머지는 `Decimal`로
  가르면 같은 값이 라우트마다 다른 타입으로 나간다.
- **시각은 UTC ISO 8601에 `Z`다.** 시간대 변환은 프론트 몫이라는 것이 프로젝트 규칙이다.
  `run_date`만 KST 세션 날짜이고 그것은 `date`라 해당 없다.
"""

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def _serialize_utc(value: datetime) -> str:
    """`UtcDatetime` 전용.

    표기 규칙의 원본은 `apps/core/utility.utc_text`이지만 이 함수는 `None`을 안 받는다 —
    애노테이션이 `UtcDatetime | None`이어도 Pydantic은 datetime 쪽 가지에서만 부른다.
    """
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


UtcDatetime = Annotated[datetime, PlainSerializer(_serialize_utc, return_type=str)]


class ApiModel(BaseModel):
    """응답 모델의 공통 형태. 만든 뒤 바뀌지 않는다."""

    model_config = ConfigDict(frozen=True)


class Page[T](ApiModel):
    """목록 응답의 공통 형태. **행을 주는 모든 라우트가 이것이다.**

    래퍼를 리소스마다 손으로 쓰면 어느 하나에서 `has_more`를 빠뜨리고, 그 라우트만
    조용히 전부를 준다. 제네릭 하나로 두면 그럴 자리가 없다.

    ## `has_more`이지 `total`이 아니다

    **총 건수를 세지 않는다.** `count(*)`는 조회를 두 번 하게 만들고, 이 저장소의 표는
    분봉 53만 행짜리도 있어서 그 두 번째가 첫 번째보다 비싸다. 대신 `limit + 1`을 읽어
    다음 쪽이 있는지만 본다 — 화면에 필요한 것은 "다음" 버튼을 켤지 끌지뿐이다.

    실질 페이지네이션은 **구간을 좁히는 것**이다. 대부분의 자연키가 날짜를 갖고 있어
    날짜를 좁히면 결과가 곧 한 쪽이 된다.
    """

    items: tuple[T, ...] = ()
    limit: int = Field(description="요청한 쪽 크기.")
    offset: int = Field(description="건너뛴 건수.")
    has_more: bool = Field(
        default=False,
        description="다음 쪽이 있나. `limit + 1`건을 읽어 판단한다 — **총 건수는 세지 않는다.**",
    )
