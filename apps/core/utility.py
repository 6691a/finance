"""`apps/` 트리가 함께 쓰는 변환.

`airflow/modules/utility.py`의 대칭 자리다. 두 트리는 서로를 import하지 않으므로 같은
규칙이 양쪽에 한 벌씩 있고, 어긋나면 테스트가 잡는다(중복 허용 + 대조 규칙).

**여기 두는 것은 상태가 없는 변환뿐이다.** 설정을 읽거나 연결을 쥐는 것은 `config.py`·
`database.py`·`redis.py`가 갖는다. 이 모듈은 그 어느 것도 import하지 않아서 어디서든
불러도 `config.yaml`을 요구하지 않는다.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

# 국내 세션 날짜의 기준. **고정 offset이 아니라 IANA 시간대를 쓴다**(프로젝트 규칙) —
# DST와 과거 시간대 변경을 직접 계산하지 않는다.
KST = ZoneInfo("Asia/Seoul")


def utc_text(value: datetime | None) -> str | None:
    """`2026-08-26T03:35:00Z`. **API 응답 시각 표기의 원본은 이 함수 하나다.**

    프로젝트 규칙이 `Z`를 요구하는데 Python `isoformat()`과 Pydantic 기본 직렬화는 둘 다
    `+00:00`을 낸다. 그 치환이 여러 자리에 복사되면 한쪽만 고친 날 한 응답 안에서 두 표기가
    갈린다.

    Pydantic 모델 필드는 이것을 직접 부르지 않고 `apps/api/schemas/common.py`의
    `UtcDatetime`을 쓴다. 이 함수는 **모델 밖**의 값(그래프 노드 속성처럼 맨 dict에 담기는
    것)을 위한 것이다.
    """
    return None if value is None else value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def kst_today() -> date:
    """KST 오늘. 국내 세션 날짜(`run_date`)와 같은 축이다.

    UTC 날짜를 쓰면 08:00 KST 이전에 하루가 어긋난다.
    """
    return datetime.now(UTC).astimezone(KST).date()
