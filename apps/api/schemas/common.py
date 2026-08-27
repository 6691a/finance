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

from pydantic import BaseModel, ConfigDict, PlainSerializer


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
