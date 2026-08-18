"""최근 값 하나를 지난 며칠과 견줘 "이게 큰 값인가"에 답하는 층.

리포트가 `코스피 +0.82%`만 주면 읽는 쪽도 요약을 쓰는 모델도 그게 큰 값인지 알 수 없다.
판단에 필요한 것은 데이터 접근 권한이 아니라 **비교 기준**이다. 그래서 값을 더 주는 대신
지난 구간과의 관계를 미리 계산해 넣는다.

**계산은 여기서 하고 모델은 읽기만 한다.** 숫자를 만드는 것은 SQL과 이 모듈이라는 경계는
`docs/economic-document-archive-design.md` §1과 같다.

## 금리와 가격을 같은 자로 재지 않는다

금리에 퍼센트 변화를 씌우면 4.00 → 4.10과 0.40 → 0.50이 전혀 다른 크기가 되고, 유로 지역은
마이너스 구간이 있어 부호까지 뒤집힌다. 금리는 변화폭(bp), 가격·환율은 퍼센트다
(설계 문서 §8.2와 같은 규칙).

가격 쪽에 설계 문서가 말하는 로그 수익률 대신 퍼센트를 쓴다. 여기서 변화량을 쓰는 곳이
**순위(백분위)뿐**이라 두 척도의 결과가 같다. 로그가 필요해지는 것은 상관·회귀를 낼 때이고
그건 4단계의 일이다.

## 표본이 짧다는 사실은 감추지 않는다

연휴 뒤나 새로 붙인 계열은 관측이 몇 개뿐이다. 그 위에서 낸 백분위는 이야기가 아니라
잡음이다. 값을 숨기는 대신 `thin`과 `observations`를 함께 실어 보내고, 프롬프트가
"표본이 적으면 근거로 쓰지 않는다"를 말한다.
"""

from collections.abc import Sequence
from datetime import date
from enum import StrEnum
from itertools import pairwise

from pydantic import BaseModel, ConfigDict

# 이보다 관측이 적으면 백분위를 근거로 쓰지 말라고 표시한다.
MIN_MEANINGFUL_OBSERVATIONS = 10

PERCENT = 100.0
BASIS_POINTS = 100.0


class ChangeKind(StrEnum):
    """변화를 무엇으로 재는가."""

    ABSOLUTE = "absolute"
    """금리. 변화폭을 bp로 잰다."""

    RELATIVE = "relative"
    """가격·환율·지수. 변화를 퍼센트로 잰다."""


class Trend(BaseModel):
    """한 계열의 최근 변화와 그 변화가 지난 구간에서 갖는 위치."""

    model_config = ConfigDict(frozen=True)

    observations: int
    """구간 안 관측 수. 백분위를 믿을지 정하는 근거다."""

    change: float
    """마지막 변화. 금리는 bp, 그 밖은 퍼센트."""

    move_percentile: float
    """이번 변화의 크기가 구간 안 변화들 중 몇 번째인가(0~100). 방향이 아니라 크기다."""

    streak: int
    """같은 방향이 이어진 날 수. 부호가 방향이다(`-3`이면 3일 연속 하락)."""

    window_low: float
    window_high: float

    thin: bool
    """관측이 `MIN_MEANINGFUL_OBSERVATIONS`보다 적다. 백분위를 근거로 쓰지 않는다."""


def summarize(points: Sequence[tuple[date, float]], kind: ChangeKind) -> Trend | None:
    """날짜 오름차순 (날짜, 값)에서 추세를 뽑는다. 변화를 낼 수 없으면 `None`."""
    values = [float(value) for _, value in points]
    if len(values) < 2:
        return None

    changes = [_change(previous, current, kind) for previous, current in pairwise(values)]
    latest = changes[-1]
    return Trend(
        observations=len(values),
        change=latest,
        move_percentile=_percentile(abs(latest), [abs(change) for change in changes]),
        streak=_streak(changes),
        window_low=min(values),
        window_high=max(values),
        thin=len(values) < MIN_MEANINGFUL_OBSERVATIONS,
    )


def sign_streak(values: Sequence[float]) -> int:
    """마지막 값과 부호가 같은 날이 며칠째인가. 부호가 방향이다(`-5`는 5일 연속 마이너스).

    **수급은 이걸로 센다.** "외국인 5일 연속 순매도"는 금액이 계속 마이너스였다는 뜻이지
    금액이 매일 줄었다는 뜻이 아니다. `Trend.streak`은 변화 방향을 세므로 순매도가 잦아드는
    날 흐름이 끊긴 것처럼 보인다. 값 자체에 부호가 있는 계열에는 그 셈이 맞지 않는다.

    0인 날은 어느 쪽도 아니라 흐름을 끊는다.
    """
    if not values:
        return 0
    direction = _sign(values[-1])
    if direction == 0:
        return 0
    length = 0
    for value in reversed(values):
        if _sign(value) != direction:
            break
        length += 1
    return length * direction


def _change(previous: float, current: float, kind: ChangeKind) -> float:
    if kind is ChangeKind.ABSOLUTE:
        return (current - previous) * BASIS_POINTS
    if not previous:
        return 0.0
    return (current - previous) / previous * PERCENT


def _percentile(value: float, population: Sequence[float]) -> float:
    """`value`보다 작은 값의 비율. 같은 값이 여럿이면 그 절반을 센다.

    동점을 반씩 나누지 않으면 변화가 없는 날들이 모두 0번째가 되어, 조용한 구간의
    작은 움직임이 늘 "최대 변화"로 올라온다.
    """
    if not population:
        return 0.0
    below = sum(1 for item in population if item < value)
    equal = sum(1 for item in population if item == value)
    return (below + equal / 2) / len(population) * PERCENT


def _streak(changes: Sequence[float]) -> int:
    """마지막 변화와 같은 방향이 이어진 날 수. 변화가 0이면 흐름이 끊긴 것으로 본다."""
    direction = _sign(changes[-1])
    if direction == 0:
        return 0
    length = 0
    for change in reversed(changes):
        if _sign(change) != direction:
            break
        length += 1
    return length * direction


def _sign(value: float) -> int:
    if value > 0:
        return 1
    return -1 if value < 0 else 0
