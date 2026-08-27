"""서비스들이 공유하는 변환.

시각 표기(`utc_text`)는 Airflow 쪽과도 답이 같아야 해서 `apps/core/utility.py`에 있다.
여기 있는 것은 **응답 계약에만 걸린 판단** — JSON에 숫자를 어떻게 싣느냐다.
"""

from decimal import Decimal


def number(value: Decimal | float | None) -> float | None:
    """`Decimal`을 JSON number로.

    그대로 두면 Pydantic이 문자열로 직렬화해 클라이언트가 매번 파싱한다. 표시용이고
    유효자리가 넷 이하라(`Numeric(5,4)`·`Numeric(8,4)`) 왕복이 안전하다.
    """
    return None if value is None else float(value)
