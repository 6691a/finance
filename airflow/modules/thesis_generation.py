"""추론 생성 — 답변 모델, 프롬프트, `ThesisBuilder`.

관측 상태와 툴 결과를 놓고 오늘의 방향을 확률로 적는다. 흐름은 LangGraph `StateGraph`이고
그래프는 생성자에서 한 번 compile한다.

저장은 여기 없다. 만든 초안을 쓰는 것은 `thesis_store.ThesisStore`다 — 조회와 저장의
트랜잭션 경계를 DAG이 쥐어야 하기 때문이다.
"""

"""시장 추론(thesis)을 만들고, 저장하고, 채점한다.

**맞고 틀림이 목적이 아니다.** "어떤 정보를 근거로 어떤 결론을 냈다"가 기록으로 남는 것이
목적이다. 채점은 그 기록 위에 나중에 얹히고, 틀린 판단도 고치지 않는다.

## 근거는 고정 풀이 아니라 모델이 조회한다

프롬프트에는 **관측 상태만** 준다("코스피 +1.61%", "SK하이닉스 전일 -2.1%"). 관측 상태는
전부 SQL이 계산한다. 왜인지 알아내는 데 필요한 정보는 모델이 `ThesisToolbox`의 읽기 전용 툴을
호출해 스스로 가져온다 — 어떤 것을 얼마나 볼지는 모델이 정한다.

**모델이 실제로 인용한 근거만 저장한다.** 툴이 돌려준 항목에는 전부 `ref`가 붙어 있고,
답변의 `evidence_refs`는 그 레지스트리로 검증한다. 목록 밖 ref는 버린다. 이것이 모델이 근거를
지어내지 못하게 막는 유일한 장치다.

## 조사와 답변을 나눈다

`modules/llm.py`의 원칙 그대로다. 조사 단계는 툴만 바인딩하고, 답변 단계는 툴을 빼고
`response_format`을 강제한다. 한 요청에 둘을 섞지 않는다 — `llm.invoke`가 그것을 막는다.

## 기준 시각은 벽시계가 아니다

**모든 조회의 끝은 슬롯이 정한 `as_of_at`이다.** 오후에 장전 슬롯을 다시 돌려도 장중 정보로
아침 예측을 덮지 않는다. 이것은 event-time cutoff다 — 현재 DB에서 확인 가능한 범위에서
`as_of_at` 이후 감지·평가·갱신된 행을 뺀다. 과거 시점을 완전히 복원하지는 못한다
(`document`는 본문·평가를 같은 행에 덮어쓰고 버전 이력을 두지 않는다).

## 첫 성공본은 불변이다

같은 (날짜, 슬롯)에 추론 행이 이미 있으면 LLM을 다시 부르지 않는다. LLM은 재호출마다 답이
달라서 덮어쓰면 최초 판단이 사라진다. `existing_theses`가 먼저 보고, 없을 때만 Builder를 돈다.

## 채점에 LLM이 없다

수식이 SQL이 아니라 파이썬에 있는 이유는 경계값을 DB 없이 테스트하기 위해서다(테스트에서
실 DB를 쓰지 않는 프로젝트 규칙). `select_session_return.sql`이 등락률을 주고
`update_outcome.sql`은 여기서 나온 값 넷을 쓰기만 한다.

설계는 `docs/analysis/market-thesis/1-storage.md`와 `docs/analysis/market-thesis/2-agent.md`에 있다.
"""

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from types import MappingProxyType
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from modules import llm
from modules.base_rate import FLAT_BASE_RATE_BARS
from modules.llm import UnsupportedResponseFormat
from modules.schema import SchemaError, json_object, response_format
from modules.thesis_domain import (
    FLAT_THRESHOLD_PCT,
    MAX_MECHANISM_CHARS,
    MAX_REASONING_CHARS,
    MAX_TOOL_ROUNDS,
    PROB_QUANTUM,
    PROB_SUM_TOLERANCE,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    VOLUME_HEAVY_RATIO,
    VOLUME_LIGHT_RATIO,
    Subject,
    ThesisDirection,
    ThesisError,
    _shorten,
    _shorten_to,
    kst_label,
)
from modules.thesis_state import (
    INTRADAY_SLOTS,
    NxtObservedState,
    ObservedState,
    PastThesis,
    RunSlot,
    SameDayThesis,
)
from modules.thesis_toolbox import (
    ThesisToolbox,
    tool_node,
)

logger = logging.getLogger(__name__)


class ClaimAnswer(BaseModel):
    """모델이 근거 하나를 어떻게 썼는지. 검증 전 원본이다.

    이유 문장은 산문이라 그래프 엣지에 실을 수 없다. 근거마다 **방향과 경로**를 따로 받아야
    `(:Thesis)-[:CITES {direction, mechanism}]->(:Evidence)`가 된다.
    """

    model_config = ConfigDict(frozen=True)

    ref: str
    direction: Literal["up", "down", "flat"]
    mechanism: str = ""


class ThesisAnswer(BaseModel):
    """모델이 subject 하나에 대해 낸 답. 검증 전 원본이다."""

    model_config = ConfigDict(frozen=True)

    subject_code: str
    prob_up: float = Field(ge=0, le=1)
    prob_down: float = Field(ge=0, le=1)
    prob_flat: float = Field(ge=0, le=1)
    up_reasoning: str = ""
    down_reasoning: str = ""
    flat_reasoning: str = ""
    claims: tuple[ClaimAnswer, ...] = ()


class Answers(BaseModel):
    """모델 응답 전체. 스키마를 강제하되 강제가 안 되는 제공처를 위해 검증도 남긴다."""

    model_config = ConfigDict(frozen=True)

    theses: tuple[ThesisAnswer, ...] = ()


class Claim(BaseModel):
    """레지스트리로 검증을 마친 인용 하나. `thesis_evidence` 행의 direction·mechanism이 된다."""

    model_config = ConfigDict(frozen=True)

    ref: str
    direction: ThesisDirection
    mechanism: str


class ThesisDraft(BaseModel):
    """검증·정규화를 마친 추론 하나. 그대로 `thesis` 행이 된다."""

    model_config = ConfigDict(frozen=True)

    subject: Subject
    prob_up: Decimal
    prob_down: Decimal
    prob_flat: Decimal
    up_reasoning: str
    down_reasoning: str
    flat_reasoning: str
    # 레지스트리로 검증하고 ref 첫 등장 순서로 중복을 없앤 인용. rank는 이 순서다.
    claims: tuple[Claim, ...] = ()

    @property
    def evidence_refs(self) -> tuple[str, ...]:
        return tuple(claim.ref for claim in self.claims)


def normalize_probabilities(
    prob_up: float,
    prob_down: float,
    prob_flat: float,
) -> tuple[Decimal, Decimal, Decimal] | None:
    """세 확률의 합을 정확히 1로 맞춘다. 허용 오차를 넘으면 `None`이다.

    모델에게 직접 1로 맞춰 달라고 프롬프트에 적어 두고, 여기서는 반올림·형식 오차만 흡수한다.
    합이 `PROB_SUM_TOLERANCE`를 넘게 어긋났다는 것은 모델이 규칙을 안 지켰다는 뜻이라
    그 subject를 버린다 — 억지로 정규화하면 모델이 부르지 않은 확률을 우리가 지어내게 된다.
    """
    values = [Decimal(str(prob_up)), Decimal(str(prob_down)), Decimal(str(prob_flat))]
    total = sum(values)
    if total <= 0 or abs(total - 1) > PROB_SUM_TOLERANCE:
        return None

    scaled = [(value / total).quantize(PROB_QUANTUM, rounding=ROUND_HALF_UP) for value in values]
    # 자리수를 맞추면서 생긴 잔차를 가장 큰 칸에 몰아 준다. DB CHECK가 합 오차 0.001 미만을
    # 요구하므로 여기서 정확히 1이 되어야 한다.
    residual = Decimal(1) - sum(scaled)
    largest = max(range(3), key=lambda index: scaled[index])
    scaled[largest] += residual
    return scaled[0], scaled[1], scaled[2]


# ---------------------------------------------------------------------------
# 프롬프트
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = f"""너는 시장 추론 기록기다. 주어진 관측 상태를 읽고, 왜 그렇게 움직였는지 또는
움직일 것 같은지를 **가설로** 적는다.

**너는 예측 정확도로 평가받지 않는다.** 맞고 틀림은 시간이 지나야 알고, 채점은 시스템이
자동으로 한다. 네가 할 일은 "어떤 정보를 근거로 어떤 결론을 냈다"를 남기는 것이다.

## 조사

근거가 필요하면 툴을 불러 직접 가져와라. 무엇을 얼마나 볼지는 네가 정한다.
조사가 끝나면 답을 낸다.

## 기술적 관측

관측 상태의 `technical`은 `as_of_date` 마감 기준의 일봉 지표이고 대상별 값이
`technical.subjects[대상코드]`에 있다. 그 대상의 지표를 낼 수 없었으면(표본 부족·가격 단절)
`null`이다. 읽는 규칙:

- `close_vs_sma20_pct`, `sma20_vs_sma60_pct`가 둘 다 양수면 단기·중기 추세가 위(정배열),
  둘 다 음수면 아래(역배열)다. 부호가 갈리면 추세 전환 구간이다.
- `rsi14`는 {int(RSI_OVERBOUGHT)} 위가 과열, {int(RSI_OVERSOLD)} 아래가 과매도다.
  그 사이는 방향 정보가 약하다.
- `macd_histogram`은 부호가 모멘텀 방향, 크기 변화가 가속·감속이다.
- `volume_ratio20`이 {VOLUME_HEAVY_RATIO} 위면 그날 움직임에 거래가 실렸다는 뜻이고
  {VOLUME_LIGHT_RATIO} 아래면 실리지 않았다는 뜻이다.
- `recent_signals`는 **교차가 일어났다는 사건**이다. 골든크로스가 곧 상승이 아니다.
  인용하려면 `ref`를 쓴다.
- 신호마다 `base_rate`가 붙는다. **같은 종류·방향의 신호가 과거에 어떻게 끝났는지**다.
  `conditional`이 그 신호 뒤의 실현 분포이고 `unconditional`이 **같은 심볼의 아무 날이나**의
  실현 분포다. 지평(`horizon_days`)은 1·3·5 거래일이고 `up`/`flat`/`down`은 0~1 비율이다.
  - **둘의 차이가 그 신호의 정보량이다.** 신호 뒤 상승 0.60인데 평소가 0.55면 그 신호가
    더하는 것은 0.05뿐이다. `conditional`만 보고 0.60을 크게 읽지 마라.
  - 차이가 거의 없으면 그 신호는 방향 근거가 아니다. 인용하지 않는 편이 낫다.
  - `sample_size`가 작아 비율이 `null`인 것은 **재지 않았다**는 뜻이지 0이 아니다.
    `null`인 값으로 확률을 기울이지 마라.
  - 채점 임계와 같은 기준으로 분류한 값이다. 네 `prob_up`·`prob_flat`·`prob_down`과 같은
    축이라 그대로 견줘도 된다.

지표는 **가격이 이미 한 일**이다. 왜 그랬는지는 말해 주지 않는다. 뉴스·공시·수급과
맞춰 보고, 맞는 것이 없으면 지표만으로 확률을 기울이지 마라.
`daily_history` 툴은 심볼 하나의 일봉·지표·신호 이력을 더 길게 준다.

## 확률

세 확률은 **그 일이 실제로 일어날 빈도**다. 네 확신의 정도가 아니다.

- `prob_flat`은 채점 창의 등락률이 **±{FLAT_THRESHOLD_PCT[0]}% 안**에 들어올 확률이다.
  장전·장후는 그 세션 하루이고, **장중이면 지금 가격에서 마감까지**다 — 남은 시간이
  짧을수록 실제 `flat` 빈도는 아래 기준선보다 높다.
  "방향을 모르겠다"가 아니다. 모르겠으면 `prob_up`과 `prob_down`을 비슷하게 두는 것이
  맞고, `prob_flat`을 올리는 것은 틀리다. RSI 중립이나 히스토그램 0 근처는 방향 정보가
  없다는 뜻이지 등락률이 작을 것이라는 뜻이 아니다.
- **기준선은 관측 상태의 `flat_base_rate`다.** 대상마다 최근 {FLAT_BASE_RATE_BARS}거래일의
  하루 등락을 세어 `up`/`flat`/`down` 비율로 준 값이고, `sample_size`가 그 표본 수다.
  그 대상의 `flat`이 실제 실현 빈도이니 `prob_flat`의 출발점으로 삼는다. 이보다 크게 올리려면
  밴드 장세·거래량 급감처럼 **그날에 한정된 근거**를 `claims`에 대야 한다.
  - **`up`·`down`은 출발점이 아니다.** 같은 창의 실현 빈도라 그 1년이 오름세였으면 `up`이
    0.58처럼 높게 나온다. 그것을 기본값으로 삼으면 지난 1년의 방향을 오늘로 그대로 미는
    것이다. 방향은 근거가 정한다 — 근거가 없으면 `up`과 `down`을 비슷하게 둔다.
    저 둘은 `flat`을 읽을 분모로만 본다.
  - 이 값은 **대상마다 다르고 실행마다 다시 잰다.** 지수와 종목을 같은 숫자로 묶지 마라.
  - 그 대상의 키가 없거나 `flat`이 `null`이면 표본이 모자라 재지 않은 것이다. 그때만
    "국내 하루 등락은 대체로 방향이 있다"는 정도로 두고 `prob_flat`을 낮게 잡는다.
- 근거가 한쪽으로 쏠려 있으면 확률도 쏠려야 한다. 근거를 찾았는데도 세 값을 균등에
  가깝게 두면 그 기록은 아무 것도 말하지 않는다.

## 규칙

- **툴 결과와 관측 상태에 없는 사실·숫자를 쓰지 마라.** 지어낸 근거는 기록을 망친다.
- `claims`에는 인용하는 근거마다 툴이 준 `ref`, 그 근거가 대상을 미는 방향 `direction`
  (`up`/`down`/`flat`), 그 방향으로 작용하는 경로 `mechanism` 한 문장({MAX_MECHANISM_CHARS}자
  이내)을 쓴다. 목록 밖의 ref는 버려진다. 같은 ref는 한 번만 쓴다.
  인용할 것이 없으면 빈 배열로 둔다. **억지 인용이 근거 없음보다 나쁘다.**
- 세 확률 `prob_up`, `prob_down`, `prob_flat`은 각각 0~1이고 **합이 정확히 1이어야 한다.**
- 세 방향의 이유를 **모두** 쓴다. 오를 이유, 내릴 이유, 횡보할 이유가 각각 있다.
  한 방향만 쓰고 나머지를 비우지 마라 — 왜 그 반대를 배제했는지가 기록의 절반이다.
- 각 이유는 {MAX_REASONING_CHARS}자 이내의 한국어다. 넘으면 잘린다.
- 투자 조언, 매수·매도 권유, 목표가를 쓰지 마라.
- 요청 목록에 있는 subject마다 **정확히 하나씩** 답한다. 같은 subject를 두 번 쓰지 마라.

{llm.NUMBER_STYLE}

출력 형식:
{{"theses": [{{"subject_code": "", "prob_up": 0.0, "prob_down": 0.0, "prob_flat": 0.0,
 "up_reasoning": "", "down_reasoning": "", "flat_reasoning": "",
 "claims": [{{"ref": "", "direction": "up", "mechanism": ""}}]}}]}}"""

SLOT_INSTRUCTION = {
    RunSlot.PRE_OPEN: (
        "오늘 한국 장이 열리기 전이다. 밤사이 해외 시장과 전일 국내 세션을 근거로 "
        "**오늘 각 대상이 어느 방향으로 움직일지**를 가설로 적어라."
    ),
    **{
        slot: (
            "지금 한국 장이 열려 있다. 관측 상태의 `intraday`가 **기준 시각의 현재가**"
            "(`price`)와 어느 봉을 봤는지(`bar_at`), 그리고 전일 종가 대비 여기까지의 "
            "등락(`return_pct`)이다. **지금 이 가격에서 오늘 마감까지 어느 방향으로 "
            "움직일지**를 가설로 적어라. 전일 종가 대비가 아니라 **지금 가격 대비**다 — "
            "이미 오른 만큼은 네 예측에 들어가지 않는다."
        )
        for slot in INTRADAY_SLOTS
    },
    RunSlot.POST_CLOSE: ("오늘 한국 장이 닫혔다. 오늘의 세션 등락을 근거로 **왜 그렇게 움직였는지**를 가설로 적어라."),
    RunSlot.POST_NXT_CLOSE: (
        "한국 정규장(KRX)이 15:30에 닫히고 NXT 애프터마켓이 20:00에 닫혔다. 관측 상태에 "
        "정규장 등락(`regular`)과 애프터마켓 등락(`after_hours`)이 따로 있다. "
        "**정규장이 닫힌 뒤 무엇이 애프터마켓을 움직였는지**를 가설로 적어라. "
        "지수(`index_regular`)는 정규장 마감값이라 애프터마켓 움직임을 담지 않는다 — 맥락으로만 읽어라."
    ),
}

INSTRUCTION = """{slot_instruction}

기준 시각(이 시각 이후의 정보는 너에게 주어지지 않는다): {as_of_at}

**툴이 돌려주는 시각(`published_at`, `detected_at`, `window_start`, `window_end`)은 UTC다.**
한국 시장 시각으로 읽으려면 9시간을 더한다. 날짜 필드(`run_date`, `receipt_date`, `session`)는
이미 한국 기준 영업일이라 더하지 않는다.

## 추론 대상
{subjects}

## 관측 상태
```json
{observed_state}
```

## 오늘 앞 슬롯
오늘 같은 대상에 대해 **이미 낸 추론**과 그 뒤 실제로 얼마나 움직였는지다. `base_price`가
그 슬롯의 기준가, `current_price`가 지금 가격, `return_pct`가 그 사이 등락이다.

아직 채점되지 않은 값이다 — 확정 종가가 저녁에 들어오고 점수는 그때 매겨진다. 여기 있는
것은 **중간 경과**다.

앞 슬롯과 방향이 달라져도 된다. 새 정보가 그렇게 말하면 바꾸는 것이 맞다. 다만 **왜
바뀌었는지가 이유 문장에 있어야 한다.** 반대로 앞 슬롯이 이미 빗나가고 있는데 같은 이유를
그대로 반복하면 그 기록은 아무 것도 더하지 않는다.
{same_day}

## 과거 추론과 결과
같은 대상에 대해 **지난 날들에** 낸 추론과 그 채점·해설이다. `run_slot`이 종류를 가른다.

- `pre_open`과 `intraday_*`·`pre_close` — 그 시점의 **예측**이다. `outcomes`의
  `actual_return_pct`가 실제 등락이고 `brier_score`가 그 예측의 점수다(낮을수록 맞은 것).
  네가 지금 내는 것과 같은 종류다. **기준가가 슬롯마다 다르다** — 장전은 전일 종가 대비,
  장중은 그 시각 가격 대비다.
- `post_close` — 장이 닫힌 뒤 "왜 그렇게 움직였나"를 적은 **해석**이다. 예측이 아니라
  채점이 없다. 채점 칸이 비어 있다고 빗나간 예측으로 읽지 마라.

`outcomes`의 `narrative`는 며칠 뒤의 보도로 되돌아본 사후 해설이고 `verdict`는 그때의 이유가
이후 보도로 지지됐는지다(`supported`/`contradicted`/`unresolved`). **해설도 사실이 아니라
그때의 해석이다.** 같은 이유로 같은 방향을 고르고 있다면 그 이유가 이번에도 맞는지 따로
확인하라. 과거 문장을 베끼지 마라.
{past_theses}
"""

# 과거 추론이 없을 때 그 절에 넣는 말. 절 자체를 빼면 프롬프트 모양이 날마다 달라진다.
# 오늘 앞 슬롯도 같다 — 장전·장후는 언제나 "(없음)"이고 장중 첫 슬롯도 그렇다.
NO_PAST_THESES = "(없음)"

def _json_section(rows: Mapping[str, Sequence[BaseModel]]) -> str:
    """subject 코드별 모델 목록을 프롬프트 블록으로. 비면 `NO_PAST_THESES`다.

    **절 자체를 빼지 않는다.** 빼면 프롬프트 모양이 날마다 달라져 캐시도 비교도 어긋난다.
    """
    shown = {code: [row.model_dump(mode="json") for row in items] for code, items in rows.items() if items}
    if not shown:
        return NO_PAST_THESES
    return f"```json\n{json.dumps(shown, ensure_ascii=False, indent=2)}\n```"


REPAIR_INSTRUCTION = (
    "이전 응답을 쓸 수 없다. 요청 목록의 subject_code만 쓰고, 세 확률의 합을 정확히 1로 맞추고, "
    "툴이 준 ref만 인용해 JSON 객체 하나를 다시 출력하라."
)


# ---------------------------------------------------------------------------
# Builder — LangGraph
# ---------------------------------------------------------------------------


class ThesisState(TypedDict):
    """추론 한 번의 상태.

    연결·설정 객체는 넣지 않는다. 상태는 트레이스 입력으로 나간다. 레지스트리도 넣지 않는다 —
    조사 중에 자라는 값이라 Toolbox가 들고 있고 노드가 그것을 읽는다.
    """

    # `add_messages` 리듀서를 단다. 노드는 **새로 생긴 메시지만** 돌려주고 병합은
    # 리듀서가 한다 — `ToolNode`가 그 형태로 반환하므로 이게 맞춰야 할 쪽이다.
    messages: Annotated[list[BaseMessage], add_messages]
    # 요청한 대상. 답변을 거를 때 노드가 읽으므로 상태에 있어야 한다.
    subjects: tuple[Subject, ...]
    tool_rounds: int
    drafts: tuple[ThesisDraft, ...] | None
    error: str | None
    attempts: int


class ThesisBuilder:
    """관측 상태를 받아 subject마다 추론 하나를 만든다.

    흐름은 `investigate → (tool_calls 있으면) tools → investigate → … → answer →
    (형식 실패) repair → answer`다. 교정은 한 번뿐이다.

    **실행당 대화 하나에 모든 subject를 한 번에** 준다. subject마다 부르면 모델이 대상들을
    비교하지 못하고 비용도 배로 든다.
    """

    def __init__(self, model: BaseChatModel, toolbox: ThesisToolbox) -> None:
        self._model = model
        self._toolbox = toolbox
        self._schema = response_format(Answers, "market_theses")
        self._tool_node = tool_node(toolbox)
        self._graph = self._build_graph()

    @staticmethod
    def build_messages(
        *,
        run_slot: RunSlot,
        as_of_at: datetime,
        subjects: Sequence[Subject],
        observed_state: ObservedState | NxtObservedState,
        past_theses: Mapping[str, Sequence[PastThesis]],
        same_day: Mapping[str, Sequence[SameDayThesis]] = MappingProxyType({}),
    ) -> list[BaseMessage]:
        """`past_theses`는 subject 코드별 과거 추론 목록(`thesis.past_theses`의 행)이다.

        빈 매핑이면 그 절에 `NO_PAST_THESES`가 들어간다. 장후 리뷰가 그 경우다.

        `same_day`는 **오늘 앞 슬롯**의 추론과 그 뒤 실현 등락이다. 장중 슬롯만 채우고
        나머지는 비운다. 저장된 채점이 아니라 봉에서 계산한 중간 경과라 `past_theses`와
        절을 나눈다 — 섞으면 모델이 채점된 값으로 읽는다.

        **모양은 모델이 정한다**(`thesis_state`). 여기서 하는 것은 JSON으로 바꾸는 것뿐이다.
        """
        subject_lines = "\n".join(f"- {subject.code} ({subject.label}, {subject.kind.value})" for subject in subjects)
        return [
            SystemMessage(SYSTEM_PROMPT),
            HumanMessage(
                INSTRUCTION.format(
                    slot_instruction=SLOT_INSTRUCTION[run_slot],
                    as_of_at=kst_label(as_of_at),
                    subjects=subject_lines or "(없음)",
                    observed_state=json.dumps(observed_state.model_dump(mode="json"), ensure_ascii=False, indent=2),
                    same_day=_json_section(same_day),
                    past_theses=_json_section(past_theses),
                )
            ),
        ]

    def run(
        self,
        *,
        run_slot: RunSlot,
        as_of_at: datetime,
        subjects: Sequence[Subject],
        observed_state: ObservedState | NxtObservedState,
        past_theses: Mapping[str, Sequence[PastThesis]],
        same_day: Mapping[str, Sequence[SameDayThesis]] = MappingProxyType({}),
    ) -> tuple[tuple[ThesisDraft, ...], int]:
        """추론들과 툴 왕복 수. 두 번째도 실패하면 `ThesisError`를 올린다."""
        if not subjects:
            return (), 0
        state: ThesisState = {
            "messages": self.build_messages(
                run_slot=run_slot,
                as_of_at=as_of_at,
                subjects=subjects,
                observed_state=observed_state,
                past_theses=past_theses,
                same_day=same_day,
            ),
            "subjects": tuple(subjects),
            "tool_rounds": 0,
            "drafts": None,
            "error": None,
            "attempts": 0,
        }
        final = self._graph.invoke(
            state,
            config={
                "run_name": "build_theses",
                "metadata": {"run_slot": run_slot.value, "subjects": len(subjects)},
            },
        )
        drafts = final.get("drafts")
        if drafts is None:
            raise ThesisError(final.get("error") or "Model did not return any thesis")
        return drafts, final["tool_rounds"]

    def parse(self, raw: str, subjects: Sequence[Subject]) -> tuple[ThesisDraft, ...]:
        """응답을 검증하고 쓸 수 없는 항목을 버린다.

        전부 버려지면 `ThesisError`다. 그건 모델이 요청을 안 보고 답했다는 뜻이라 교정을
        요청할 값어치가 있다. 반대로 **일부만 남는 것은 정상이다** — 요청 목록에 있는데 답에
        없는 subject는 그 슬롯에 없던 것으로 남기고 재요청하지 않는다.
        """
        try:
            parsed = Answers.model_validate_json(json_object(raw))
        except SchemaError as error:
            raise ThesisError(str(error)) from error
        except ValidationError as error:
            raise ThesisError(f"Model returned an unusable object: {error}") from error

        by_code = {subject.code: subject for subject in subjects}
        seen: set[str] = set()
        duplicated: set[str] = set()
        drafts: list[ThesisDraft] = []
        dropped: list[str] = []

        for answer in parsed.theses:
            subject = by_code.get(answer.subject_code)
            if subject is None:
                dropped.append(f"{answer.subject_code}(목록 밖)")
                continue
            if answer.subject_code in seen:
                # 어느 쪽이 진짜인지 알 수 없다. 먼저 넣은 것도 함께 뺀다.
                duplicated.add(answer.subject_code)
                dropped.append(f"{answer.subject_code}(중복)")
                continue
            seen.add(answer.subject_code)
            probabilities = normalize_probabilities(answer.prob_up, answer.prob_down, answer.prob_flat)
            if probabilities is None:
                dropped.append(f"{answer.subject_code}(확률 합 {answer.prob_up + answer.prob_down + answer.prob_flat})")
                continue
            drafts.append(
                ThesisDraft(
                    subject=subject,
                    prob_up=probabilities[0],
                    prob_down=probabilities[1],
                    prob_flat=probabilities[2],
                    up_reasoning=_shorten(answer.up_reasoning),
                    down_reasoning=_shorten(answer.down_reasoning),
                    flat_reasoning=_shorten(answer.flat_reasoning),
                    claims=self._known_claims(answer),
                )
            )

        kept = tuple(draft for draft in drafts if draft.subject.code not in duplicated)
        if dropped:
            logger.warning("dropped %s theses: %s", len(dropped), dropped)
        if parsed.theses and not kept:
            raise ThesisError(f"Model returned {len(parsed.theses)} theses, none of them usable")
        return kept

    def _known_claims(self, answer: ThesisAnswer) -> tuple[Claim, ...]:
        """레지스트리에 있는 ref의 인용만, ref 첫 등장 순서로 중복 없이.

        순서가 곧 `thesis_evidence.rank`다. 같은 ref를 두 번 인용하면 **첫 것이 남는다** — 행이
        ref당 하나라 방향 둘을 담을 수 없다. 목록 밖 ref는 버리고 건수를 로그로 남긴다 —
        조용히 버리면 모델이 무엇을 지어내는지 알 수 없다.
        """
        registry = self._toolbox.registry
        kept: dict[str, Claim] = {}
        unknown: list[str] = []
        for claim in answer.claims:
            if claim.ref not in registry:
                unknown.append(claim.ref)
            elif claim.ref not in kept:
                kept[claim.ref] = Claim(
                    ref=claim.ref,
                    direction=ThesisDirection(claim.direction),
                    mechanism=_shorten_to(claim.mechanism, MAX_MECHANISM_CHARS),
                )
        if unknown:
            logger.warning("%s cited %s refs that no tool returned: %s", answer.subject_code, len(unknown), unknown)
        return tuple(kept.values())

    def _build_graph(self):
        graph = StateGraph(ThesisState)
        graph.add_node("investigate", self._investigate)
        graph.add_node("tools", self._tools)
        graph.add_node("answer", self._answer)
        graph.add_node("repair", self._repair)
        graph.add_edge(START, "investigate")
        graph.add_conditional_edges("investigate", self._after_investigate, {"tools": "tools", "answer": "answer"})
        graph.add_edge("tools", "investigate")
        graph.add_conditional_edges("answer", self._after_answer, {"repair": "repair", END: END})
        graph.add_edge("repair", "answer")
        return graph.compile()

    def _investigate(self, state: ThesisState) -> dict[str, Any]:
        """툴만 바인딩해 부른다. 스키마는 넣지 않는다(`llm.invoke`가 막는다)."""
        reply = llm.invoke(self._model, state["messages"], tools=self._toolbox.tools)
        return {"messages": [reply]}

    def _tools(self, state: ThesisState) -> dict[str, Any]:
        """`ToolNode`가 tool_call을 돌리고 `ToolMessage`를 만든다. 우리는 왕복만 센다.

        **`tool_call_id`마다 `ToolMessage`가 정확히 하나**여야 하는 것도 `ToolNode`가
        보장한다. 손으로 짜던 때는 그것이 우리 책임이었다.

        `handle_tool_errors`에 타입을 준 것이 이 노드의 핵심이다 — `ToolLimitExceeded`만
        오류 `ToolMessage`가 되어 모델이 고쳐 부를 기회를 얻고, **DB 오류는 그대로 올라가
        태스크를 죽인다.** 기본값(`True`)은 둘을 가르지 않아 연결 끊김이 "결과 없음"으로
        위장된다.
        """
        update = self._tool_node.invoke(state)
        return {"messages": update["messages"], "tool_rounds": state["tool_rounds"] + 1}

    def _answer(self, state: ThesisState) -> dict[str, Any]:
        """툴을 빼고 스키마를 강제한다. 제공처가 스키마를 안 받으면 그때만 한 번 더."""
        messages = state["messages"]
        try:
            reply = llm.invoke(self._model, messages, schema=self._schema)
        except UnsupportedResponseFormat as error:
            logger.warning("provider does not accept a response schema; falling back to validation: %s", error)
            reply = llm.invoke(self._model, messages)

        try:
            drafts = self.parse(_text(reply), state["subjects"])
        except ThesisError as error:
            return {"messages": [reply], "drafts": None, "error": str(error)}
        return {"messages": [reply], "drafts": drafts, "error": None}

    def _repair(self, state: ThesisState) -> dict[str, Any]:
        logger.warning("retrying the theses once after %s", state["error"])
        return {
            "messages": [HumanMessage(REPAIR_INSTRUCTION)],
            "attempts": state["attempts"] + 1,
        }

    @staticmethod
    def _after_investigate(state: ThesisState) -> str:
        """툴을 부르자고 했고 왕복 상한이 남았으면 조사를 잇는다."""
        reply = state["messages"][-1]
        if getattr(reply, "tool_calls", None) and state["tool_rounds"] < MAX_TOOL_ROUNDS:
            return "tools"
        return "answer"

    @staticmethod
    def _after_answer(state: ThesisState) -> str:
        if state["drafts"] is not None:
            return END
        return "repair" if state["attempts"] == 0 else END


def _text(reply: AIMessage) -> str:
    """응답 본문. 제공처에 따라 문자열이 아니라 조각 리스트로 온다."""
    content = reply.content
    if isinstance(content, str):
        return content
    return "".join(part if isinstance(part, str) else part.get("text", "") for part in content)
