"""시장 추론(thesis)의 채점.

**맞고 틀림이 목적이 아니다.** "어떤 정보를 근거로 어떤 결론을 냈다"가 기록으로 남는 것이
목적이고, 채점은 그 기록 위에 나중에 얹힌다. 그래서 여기에는 LLM이 없다 — 장전 전망이 나온
뒤 실제 세션이 끝나면 SQL이 실제 등락률을 주고, 이 모듈의 순수 함수가 그것을 분류하고
점수를 매긴다.

수식이 SQL이 아니라 파이썬에 있는 이유는 경계값을 DB 없이 테스트하기 위해서다(테스트에서
실 DB를 쓰지 않는 프로젝트 규칙). `select_session_return.sql`이 등락률을 주고
`update_outcome.sql`은 여기서 나온 값 넷을 쓰기만 한다.

설계는 `docs/market-thesis/1-storage.md`에 있다.
"""

from decimal import Decimal
from enum import StrEnum

# |등락률|이 이 값보다 작으면 방향이 없었다고 본다(퍼센트).
FLAT_THRESHOLD_PCT = Decimal("0.3")


class ThesisDirection(StrEnum):
    """방향. 예측 확률과 실제 결과가 같은 세 값을 쓴다.

    `apps/models/analysis.py`의 같은 이름 enum과 값이 같아야 한다. Airflow는 `apps/`를
    보지 못해 import하지 못하므로 값을 한 벌 더 둔다(프로젝트의 중복 허용 + 테스트 대조 규칙).
    """

    UP = "up"
    DOWN = "down"
    FLAT = "flat"


def classify_outcome(return_pct: Decimal) -> ThesisDirection:
    """실제 세션 등락률을 방향으로 분류한다.

    예측과 비교하지 않는다. 실제 움직임만 본다 — 얼마나 잘 맞췄는지는 `brier_score`가 답한다.
    경계값은 `flat` 쪽이다: 0.30은 `up`이고 0.29는 `flat`이다.
    """
    if abs(return_pct) < FLAT_THRESHOLD_PCT:
        return ThesisDirection.FLAT
    return ThesisDirection.UP if return_pct > 0 else ThesisDirection.DOWN


def brier_score(
    *,
    prob_up: Decimal,
    prob_down: Decimal,
    prob_flat: Decimal,
    outcome: ThesisDirection,
) -> Decimal:
    """3-class Brier 점수. 0이 완벽이고 2가 최악이다.

    실제 결과를 원-핫 벡터로 바꿔(`up`이면 `(1, 0, 0)`) 각 확률과의 차를 제곱해 더한다.
    방향만 맞고 확신이 지나치게 낮았던 경우와 틀린 방향에 확신을 준 경우를 함께 잡아낸다 —
    hit/miss 이분법이 놓치던 "얼마나 확신 있게 맞았나"가 점수에 실린다.

    참고값: 균등 확률(1/3씩)은 결과와 무관하게 약 0.667이다. 이것이 baseline이다.
    """
    actual = {
        ThesisDirection.UP: (1, 0, 0),
        ThesisDirection.DOWN: (0, 1, 0),
        ThesisDirection.FLAT: (0, 0, 1),
    }[outcome]
    predicted = (prob_up, prob_down, prob_flat)
    return sum(((probability - truth) ** 2 for probability, truth in zip(predicted, actual)), Decimal(0))
