"""카테고리별 분석가와 그 결과를 합치는 종합 분석가.

`docs/economic-document-archive-design.md` 4단계다. 한 모델에게 모든 데이터를 주고 "알아서
판단하라"고 하지 않는다. **카테고리마다 분석가를 두고, 각자 자기 영역만 보게 한 뒤, 그
관찰들을 모아 종합한다.**

## 왜 나누나

셋 다 한 모델에 몰아 넣으면 생기는 문제다.

- **컨텍스트가 모자란다.** 금리 41계열, 가격 27계열, 환율 10계열, 수급, 기사 수백 건을
  한 번에 넣을 방법이 없다.
- **얕게 훑는다.** 볼 것이 많을수록 모델은 눈에 띄는 하나만 붙잡고 나머지를 지나친다.
  영역을 좁히면 그 안에서 비교를 한다.
- **틀린 곳을 못 찾는다.** 결론이 하나로 뭉쳐 나오면 어느 근거가 틀렸는지 되짚을 수 없다.
  카테고리별 관찰이 따로 남으면 그 단위로 검증한다.

## 분석가는 결론을 내지 않는다

각 분석가는 **관찰만** 남긴다. "달러가 3주째 강세이고 20일 상관이 −0.6이다"까지가 분석가의
일이고, "그래서 반도체가 눌릴 것"은 종합 분석가의 일이다. 분석가가 각자 결론을 내면 서로
어긋난 결론 다섯 개를 종합 단계가 중재하게 되는데, 그건 데이터가 아니라 문장을 중재하는 것이다.

## 숫자는 툴에서만 온다

관찰의 모든 수치는 툴 응답에 있던 값이어야 한다. 종합 분석가도 분석가들의 관찰에 있던 값만
쓴다. **프롬프트에 없던 숫자가 본문에 나오면 그 리포트는 버린다.** 검증은 문자열 대조로
시작한다(§8.4).

## 카테고리를 늘리려면

`CATEGORIES`에 한 줄 더한다. 분석가 코드를 복사하지 않는다. 보이는 툴이 다르고 초점 문단이
다를 뿐, 도는 방식은 같다.
"""

import json
import logging
import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from modules.llm import ChatClient, answer
from modules.schema import response_format
from modules.tools import Connection, investigate

logger = logging.getLogger(__name__)

# 프롬프트를 고치면 올린다. 리포트에 함께 저장돼 나중에 어느 판으로 쓴 글인지 알 수 있다.
REPORT_PROMPT_VERSION = "4"

# 분석가 한 명이 부를 수 있는 최대 툴 호출 수. 없으면 모델이 목록을 끝없이 훑는다.
MAX_TOOL_CALLS = 12


class Category(BaseModel):
    """분석가 한 명의 담당 영역."""

    model_config = ConfigDict(frozen=True)

    name: str
    label: str
    focus: str
    tools: tuple[str, ...]


CATEGORIES: dict[str, Category] = {
    category.name: category
    for category in (
        Category(
            name="rates",
            label="금리·채권",
            focus=(
                "미국·한국·일본·독일·영국·유로 지역의 국채 금리 곡선과 단기 자금시장 금리를 본다. "
                "곡선의 기울기(장단기 차), 나라 사이의 벌어짐, 최근 변화 속도가 관심사다. "
                "**금리는 수익률이 아니라 변화폭으로 읽는다.** 4.0에서 4.1로 오른 것은 2.5퍼센트 상승이 "
                "아니라 10bp 상승이다."
            ),
            tools=("list_series", "get_series", "series_change", "series_spread", "compare_series"),
        ),
        Category(
            name="fx",
            label="환율·달러",
            focus=(
                "원/달러, 엔/달러, 위안/달러, 달러인덱스와 은행 고시환율을 본다. 달러의 방향과 "
                "원화가 다른 아시아 통화와 같이 움직이는지 따로 움직이는지가 관심사다. "
                "은행 고시환율(hana)은 표본이 짧으니 장외 시세(yahoo)와 구분해 쓴다."
            ),
            tools=("list_series", "get_series", "series_change", "series_spread", "compare_series"),
        ),
        Category(
            name="risk",
            label="위험자산 가격",
            focus=(
                "주가지수와 지수선물, 변동성(VIX), 반도체 지수, 원자재, 암호화폐를 본다. "
                "위험선호가 붙는지 꺼지는지, 어느 지역이 먼저 움직이는지가 관심사다. "
                "한국 정규장 시간에 미국 현물은 멈춰 있으므로 그 구간의 신호는 선물이 갖고 있다."
            ),
            tools=("list_series", "get_series", "series_change", "series_spread", "compare_series"),
        ),
        Category(
            name="flow",
            label="수급",
            focus=(
                "종목별 투자자 순매수를 본다. 외국인·기관·개인·연기금이 같은 방향인지 엇갈리는지, "
                "며칠째 이어지는지, 가격과 같이 가는지 어긋나는지가 관심사다. "
                "**수량과 대금을 섞지 않는다.** 여기서 받는 값은 주식 수다."
            ),
            tools=("get_investor_flow", "get_series", "series_change", "list_series"),
        ),
        Category(
            name="news",
            label="기사·공시",
            focus=(
                "수집한 기사와 보도자료를 본다. 어떤 사건이 반복해 나오는지, 새 수치나 정책이 "
                "있는지, 어느 종목·지표에 걸리는지가 관심사다. "
                "**기사 수가 많다고 중요한 것이 아니다.** 같은 사건을 여러 곳이 쓴 것일 수 있다."
            ),
            tools=("search_documents",),
        ),
    )
}


class Number(BaseModel):
    """관찰을 받치는 수치 하나.

    `dict[str, float]`이 아니라 이름·값 쌍의 배열이다. **열린 dict는 strict JSON Schema로
    표현할 수 없다.** 제약이 오히려 낫다. 이름이 자유 문자열이면 나중에 그 값을 찾아 쓰는
    쪽이 매번 키를 추측해야 한다.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="수치 이름. 예: correlation, observations, change_bp")
    value: float = Field(description="툴 응답에 있던 값 그대로")


class Observation(BaseModel):
    """분석가가 남기는 관찰 하나. 결론이 아니라 관찰이다."""

    model_config = ConfigDict(frozen=True)

    statement: str = Field(description="한 문장으로 쓴 관찰")
    series: tuple[str, ...] = Field(default=(), description="근거가 된 시계열 좌표")
    numbers: tuple[Number, ...] = Field(default=(), description="근거 수치. 툴 응답에 있던 값만")


class AnalystReport(BaseModel):
    """분석가 한 명의 결과."""

    model_config = ConfigDict(frozen=True)

    observations: tuple[Observation, ...] = ()
    summary: str = ""


class Claim(BaseModel):
    """종합 분석가가 남기는, 나중에 채점할 수 있는 주장."""

    model_config = ConfigDict(frozen=True)

    statement: str = Field(description="검증 가능한 형태로 쓴 주장")
    horizon_days: int = Field(ge=1, le=60, description="며칠 안의 이야기인지")
    # 검증기가 아니라 타입으로 막는다. Literal은 스키마에 enum으로 실려 모델이 애초에
    # 다른 값을 내지 못한다.
    confidence: Literal["low", "medium", "high"] = Field(description="근거의 강도")


class MarketReport(BaseModel):
    """종합 결과."""

    model_config = ConfigDict(frozen=True)

    headline: str = Field(description="한 문장 요약")
    body: str = Field(description="본문. 근거 수치를 문장 안에 그대로 쓴다")
    claims: tuple[Claim, ...] = ()
    unresolved: tuple[str, ...] = Field(default=(), description="데이터가 모자라 판단하지 못한 것")


ANALYST_SYSTEM = """\
당신은 시장 데이터 분석가다. 담당 영역은 **{label}**이다.

{focus}

## 규칙

- 담당 영역 밖은 보지 않는다. 다른 영역은 다른 분석가가 본다.
- **결론을 내지 않는다.** "그래서 무엇을 사야 한다" 같은 말을 쓰지 않는다. 관찰만 남긴다.
  종합은 다른 단계가 한다.
- 먼저 `list_series`로 무엇이 있는지 확인한다. 표본이 짧은 계열은 근거로 쓰지 않는다.
- **`get_series`는 여러 계열을 한 번에 받는다.** 계열마다 따로 부르지 마라. 그러면 호출
  예산이 나열로 다 나가고 정작 비교할 여유가 없어진다.
- **값을 나열하는 것은 조사가 아니다.** 시작값과 끝값을 관찰에 옮겨 적고 "높아졌다"로
  끝내지 마라. 변화폭은 `series_change`가, 곡선 기울기와 나라 사이 벌어짐은
  `series_spread`가, 함께 움직이는 정도는 `compare_series`가 계산해 준다.
- **대상 기간은 무엇을 서술할지의 범위이지 조사할 구간의 상한이 아니다.** 관계를 볼 때는 더
  긴 창을 써도 된다. 7거래일짜리 상관은 잡음이다.
- **모든 수치는 툴 응답에 있던 값이어야 한다.** 계산이 필요하면 툴을 부른다. 암산하지 않는다.
- 상관을 볼 때는 관측 수를 함께 본다. 표본이 적다는 경고가 오면 그 값을 근거로 쓰지 않는다.
- **계열마다 마지막 관측일이 다르다.** 제공처의 발표 시차 때문이다. 나라를 견주기 전에
  `list_series`의 `last_date`를 확인하고, 구간이 어긋나면 그 사실을 관찰에 적는다.
- 툴 호출은 {max_calls}회를 넘기지 않는다. 남기고 끝내도 된다.

## 출력

툴 조사를 마치면 JSON 객체 하나만 출력한다. 설명이나 코드 펜스를 붙이지 않는다.

{{"observations": [{{"statement": "", "series": [], "numbers": {{}}}}], "summary": ""}}
"""

SYNTHESIS_SYSTEM = """\
당신은 여러 분석가의 관찰을 모아 시장 상황을 서술하는 종합 분석가다.

## 규칙

- **한국어로 쓴다.** 분석가 관찰이 영어여도 본문은 한국어다. 시계열 좌표와 지표 이름은
  원문 그대로 둔다.
- **분석가들이 남긴 관찰과 수치만 쓴다.** 프롬프트에 없는 숫자를 본문에 쓰지 않는다.
  기억에 있는 값이나 짐작한 값을 넣으면 그 리포트는 버려진다.
- **관찰을 옮겨 적지 마라.** 나열은 종합이 아니다. 서로 맞물리는 것과 어긋나는 것을 짚고,
  왜 그렇게 보이는지를 쓴다. 근거로 쓰는 수치만 본문에 넣고 나머지는 버린다.
- 서로 어긋나는 관찰이 있으면 감추지 말고 어긋난다는 사실을 쓴다.
- `headline`은 한 문장이다. 관찰을 다 욱여넣지 마라.

## claims

**아직 일어나지 않은 것만 쓴다.** 이미 관측된 사실을 다시 적는 것은 주장이 아니다.
"DGS10이 기간 말 4.63이었다"는 관찰이고, "향후 5거래일 DGS10이 4.63보다 낮아진다"가 주장이다.

- 나중에 데이터로 맞았는지 확인할 수 있는 형태로 쓴다. 대상, 방향, 기간이 있어야 한다.
- 근거가 약하면 `confidence`를 낮추고, 쓸 만한 주장이 없으면 **빈 배열로 둔다.**
- 근거가 모자라 판단하지 못한 것은 `unresolved`에 남긴다. 억지로 만들지 않는다.

## 출력

JSON 객체 하나만 출력한다. 설명이나 코드 펜스를 붙이지 않는다.

{"headline": "", "body": "",
 "claims": [{"statement": "", "horizon_days": 5, "confidence": "low"}],
 "unresolved": []}
"""


class AnalysisError(RuntimeError):
    """모델이 우리가 아는 모양으로 답하지 않았다."""


def analyst_messages(category: Category, brief: str) -> list[dict[str, str]]:
    """분석가 한 명에게 보낼 첫 메시지.

    `brief`는 대상 기간처럼 모든 분석가가 공유하는 짧은 맥락이다. 데이터는 여기 넣지 않는다.
    분석가가 툴로 직접 가져오게 하는 것이 이 설계의 요점이다.
    """
    system = ANALYST_SYSTEM.format(label=category.label, focus=category.focus, max_calls=MAX_TOOL_CALLS)
    return [{"role": "system", "content": system}, {"role": "user", "content": brief}]


def synthesis_messages(brief: str, reports: Sequence[tuple[str, AnalystReport]]) -> list[dict[str, str]]:
    """종합 분석가에게 보낼 메시지. **원자료가 아니라 분석가들의 관찰만 넣는다.**"""
    parts = [brief]
    for name, report in reports:
        category = CATEGORIES[name]
        payload = json.dumps(report.model_dump(), ensure_ascii=False, indent=1)
        parts.append(f"\n## {category.label} 분석가\n{payload}")
    return [{"role": "system", "content": SYNTHESIS_SYSTEM}, {"role": "user", "content": "\n".join(parts)}]


def _json_object(raw: str) -> str:
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        raise AnalysisError("Model did not return a JSON object")
    return raw[start : end + 1]


def parse_analyst_report(raw: str) -> AnalystReport:
    try:
        return AnalystReport.model_validate_json(_json_object(raw))
    except ValidationError as error:
        raise AnalysisError(f"Model returned an invalid analyst report: {error}") from None


def parse_market_report(raw: str) -> MarketReport:
    try:
        return MarketReport.model_validate_json(_json_object(raw))
    except ValidationError as error:
        raise AnalysisError(f"Model returned an invalid market report: {error}") from None


def unsupported_numbers(report: MarketReport, reports: Sequence[tuple[str, AnalystReport]]) -> tuple[str, ...]:
    """본문에 나오는데 분석가 관찰에는 없는 숫자.

    **비면 통과, 비지 않으면 그 리포트는 버린다.** 모델이 데이터를 보지 않고도 그럴듯한
    문장을 만드는 것을 막는 유일한 자동 검사다.

    문자열 대조라 완벽하지 않다. 연도나 일수처럼 근거가 아닌 숫자도 걸린다. 그래서 관찰의
    수치와 좌표에 등장한 값, 그리고 `claims`가 쓰는 기간은 허용 목록에 넣는다.
    """
    allowed: set[str] = set()
    for _, analyst in reports:
        for observation in analyst.observations:
            allowed.update(_number_tokens(observation.statement))
            for number in observation.numbers:
                allowed.update(_number_forms(number.value))
            allowed.update(_number_tokens(" ".join(observation.series)))
        allowed.update(_number_tokens(analyst.summary))
    allowed.update(str(claim.horizon_days) for claim in report.claims)

    used = _number_tokens(report.body) | _number_tokens(report.headline)
    return tuple(sorted(token for token in used if token not in allowed))


def _number_tokens(text: str) -> set[str]:
    # 부호와 소수점을 포함해 숫자로 읽히는 조각을 뽑는다. 천 단위 쉼표는 떼고 비교한다.
    return {_normalized(match.replace(",", "")) for match in re.findall(r"-?\d[\d,]*\.?\d*", text)}


def _normalized(token: str) -> str:
    """비교용 표기. `1.70`과 `1.7`은 같은 숫자다."""
    if "." in token:
        token = token.rstrip("0").rstrip(".")
    return token or "0"


def _number_forms(value: float) -> set[str]:
    """본문에 쓰일 법한 표기를 모두 만든다.

    함정이 둘 있었고 둘 다 정상 리포트를 반려시켰다(실측 2026-08-15).

    - `f"{value:g}"`는 유효숫자 6자리를 넘기면 지수 표기로 바꾼다. 투자자 순매수 수량
      `-3049225`가 `-3.04922e+06`이 됐다.
    - `format(value, "f")`는 소수 6자리에서 자른다. 환율 변화 `1.70498658`이 `1.704987`이 됐다.

    그리고 **모델이 반올림해 쓰는 것은 환각이 아니다.** `1.70498658`을 근거로 받아 본문에
    `1.70`이라 쓰는 것은 정상이다. 그래서 정확한 표기와 흔한 반올림을 함께 허용한다.
    """
    if value == int(value):
        return {str(int(value))}
    forms = {repr(float(value))}
    forms.update(f"{value:.{digits}f}" for digits in range(1, 7))
    return {_normalized(form) for form in forms}


# 조사를 마친 뒤 스키마를 강제해 받는 마지막 요청. 툴은 이 요청에 넣지 않는다.
ANALYST_FINAL_INSTRUCTION = (
    "조사를 마쳤다. 지금까지 툴에서 받은 값만 써서 관찰을 JSON으로 정리하라. 툴 응답에 없던 숫자를 쓰지 마라."
)
SYNTHESIS_FINAL_INSTRUCTION = "위 분석가들의 관찰만 써서 종합 리포트를 JSON으로 작성하라."


def run_analyst(
    client: ChatClient,
    connection: Connection,
    model: str,
    category: Category,
    brief: str,
    max_calls: int = MAX_TOOL_CALLS,
) -> tuple[AnalystReport, int]:
    """분석가 한 명을 돌린다. (결과, 툴 호출 수)를 돌려준다.

    조사와 답변을 나눈 이유는 `modules/llm.py`에 있다. 조사 turn에는 툴만, 답변 turn에는
    스키마만 준다.
    """
    conversation, used = investigate(
        client, connection, model, analyst_messages(category, brief), category.tools, max_calls
    )
    raw = answer(
        client,
        model,
        conversation,
        response_format(AnalystReport, "analyst_report"),
        ANALYST_FINAL_INSTRUCTION,
    )
    return parse_analyst_report(raw), used


def run_synthesis(
    client: ChatClient,
    model: str,
    brief: str,
    reports: Sequence[tuple[str, AnalystReport]],
) -> MarketReport:
    """종합 분석가를 돌린다. **툴을 주지 않는다.** 원자료를 다시 읽을 이유가 없다."""
    raw = answer(
        client,
        model,
        synthesis_messages(brief, reports),
        response_format(MarketReport, "market_report"),
        SYNTHESIS_FINAL_INSTRUCTION,
    )
    return parse_market_report(raw)
