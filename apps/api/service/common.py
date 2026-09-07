"""서비스들이 공유하는 변환.

시각 표기(`utc_text`)는 Airflow 쪽과도 답이 같아야 해서 `apps/core/utility.py`에 있다.
여기 있는 것은 **응답 계약에만 걸린 판단** — JSON에 숫자를 어떻게 싣느냐다.
"""

from decimal import Decimal

# DART 뷰어 주소. **접수번호만 있으면 사람이 원문을 열 수 있다.**
# `modules.briefing.disclosures.DART_VIEWER_URL`과 같은 값이고 두 트리가 서로를 import하지 않아
# 중복을 허용한다 — `tests/api/test_dart_links.py`가 둘을 대조한다.
DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"


def dart_url(provider: str, rcept_no: str) -> str | None:
    """**제공처가 `dart`일 때만 만든다.** 다른 제공처의 접수번호를 DART 주소에 끼우면
    열리지 않는 링크가 생기고, 그건 없는 것보다 나쁘다."""
    return DART_VIEWER.format(rcept_no=rcept_no) if provider == "dart" else None


def number(value: Decimal | float | None) -> float | None:
    """`Decimal`을 JSON number로.

    그대로 두면 Pydantic이 문자열로 직렬화해 클라이언트가 매번 파싱한다. 표시용이고
    유효자리가 넷 이하라(`Numeric(5,4)`·`Numeric(8,4)`) 왕복이 안전하다.
    """
    return None if value is None else float(value)
