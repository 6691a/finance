"""모델이 부르는 툴의 인자 스키마와 설명.

**툴 본체(`toolbox.py`)와 갈라 둔다.** 이 파일이 바뀌는 이유는 모델에게 무엇을 어떻게
부르라고 말할지이고, 저쪽이 바뀌는 이유는 그 답을 어떤 SQL로 만드느냐다. 문장은 프롬프트
YAML에 두지 않는다 — 상한 값을 `Field(description=...)`에 f-string으로 싣는 것이 규칙이라
스키마 선언과 한 몸이고, 떼면 둘이 어긋난다(`writing-llm-flows` 스킬 "옮기지 않는 것 셋").
"""

from typing import Annotated, Any

from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, ConfigDict, Field, model_validator

from modules.thesis.domain import (
    MAX_HISTORY_DAYS,
    MAX_PAST_THESES,
    MAX_WINDOW_HOURS,
    MIN_HISTORY_DAYS,
    MIN_PAST_THESES,
    MIN_VALUE_SCORE,
    MIN_WINDOW_HOURS,
)

# 툴 인자는 **Pydantic 모델로 선언한다.** JSON Schema는 LangChain이 뽑는다 — 손으로 쓴
# `{"type": "function", ...}` dict는 제공처 wire format이라 이름·타입이 코드와 어긋나도
# 아무도 못 잡는다(2026-08-21 전환).


class ToolArgs(BaseModel):
    """툴 인자의 공통 규칙.

    **못 읽는 값은 거절하지 않고 기본값으로 되돌린다.** 모델이 `hours`에 `"bad"`나 null을
    넣어도 왕복 하나를 오타에 쓰지 않는다. 범위를 자르는 것은 각 툴의 `clamp_int`다
    (`docs/analysis/market-thesis/2-agent.md` 1절 "상한은 코드 상수로 강제한다 — 모델이 인자를
    넘겨도 잘라서 실행한다").

    거절하는 것은 이 층이 아니라 위다: 모르는 툴 이름과 상한 초과는 `ToolLimitExceeded`가
    되어 오류 `ToolMessage`로 모델에게 돌아간다.
    """

    model_config = ConfigDict(extra="ignore")

    # **모델에게 보이지 않는 칸이다.** `InjectedToolCallId`가 붙으면 LangChain이
    # `tool_call_schema`에서 빼고(`BaseTool.args`도 그것을 쓴다) 실행 시점에 진짜 call id로
    # 채운다. 모델이 위조해 보내도 `BaseTool._parse_input`이 덮는다. 이 한 칸이 있어야
    # 공통 래퍼가 "지금 부른 것이 어느 요청이었나"를 안다 — 툴 14개에 계측 코드를 따로
    # 넣지 않는 이유다. 여기 두는 것은 모든 툴 인자가 `ToolArgs`를 상속해서다.
    tool_call_id: Annotated[str, InjectedToolCallId] = ""

    @model_validator(mode="before")
    @classmethod
    def _drop_unreadable(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        cleaned = dict(data)
        for name, field in cls.model_fields.items():
            if name not in cleaned:
                continue
            value = cleaned[name]
            caster = field.annotation
            if value is None or not callable(caster):
                cleaned.pop(name)
                continue
            try:
                cleaned[name] = caster(value)
            except (TypeError, ValueError):
                # 키를 빼면 필드 기본값이 들어간다. 그 기본값이 곧 fallback이다.
                cleaned.pop(name)
        return cleaned


class RecentDocumentsArgs(ToolArgs):
    hours: int = Field(
        default=MAX_WINDOW_HOURS,
        description=f"기준 시각에서 거슬러 올라갈 시간. {MIN_WINDOW_HOURS}~{MAX_WINDOW_HOURS}.",
    )
    min_score: int = Field(
        default=MIN_VALUE_SCORE,
        description="가치 점수 하한(0~8). 낮추면 건수가 늘고 잡음도 는다.",
    )


class RecentDisclosuresArgs(ToolArgs):
    hours: int = Field(
        default=MAX_WINDOW_HOURS,
        description=f"기준 시각에서 거슬러 올라갈 시간. {MIN_WINDOW_HOURS}~{MAX_WINDOW_HOURS}.",
    )


class MacroChangesArgs(ToolArgs):
    """인자가 없다. 창은 슬롯이 정한다."""


class PastThesesArgs(ToolArgs):
    subject_code: str = Field(description="이번 실행의 대상 목록 안에 있는 값만. 다른 값은 거절된다.")
    n: int = Field(
        default=MIN_PAST_THESES,
        description=f"슬롯마다 최근 몇 건을 볼지. {MIN_PAST_THESES}~{MAX_PAST_THESES}.",
    )


class MacroIndicatorsArgs(ToolArgs):
    kind: str = Field(
        default="government_bond",
        description=(
            "볼 지표 종류. government_bond(각국 국채 금리), money_market(단기 자금시장 금리), "
            "policy_rate(중앙은행 정책금리), tips_rate(미국 10년 실질금리와 기대인플레), "
            "credit_spread(하이일드 신용스프레드), "
            "price_index(물가지수), activity(소매판매·고용 등 실물활동). "
            "**단위가 달라 한 번에 하나만 본다.** 모르는 값은 government_bond로 읽는다."
        ),
    )


class NoArgs(ToolArgs):
    """인자가 없다. 창은 슬롯이 정한다."""


class StockFlowsArgs(ToolArgs):
    days: int = Field(
        default=5,
        description=f"종목마다 최근 며칠치 확정 수급을 볼지. {MIN_HISTORY_DAYS}~{MAX_HISTORY_DAYS}.",
    )


class MarketFundsArgs(ToolArgs):
    days: int = Field(
        default=10,
        description=f"최근 며칠치 증시자금을 볼지. {MIN_HISTORY_DAYS}~{MAX_HISTORY_DAYS}.",
    )


class TypicalMoveArgs(ToolArgs):
    symbol: str = Field(
        description=(
            "이번 실행의 추론 대상 하나(KOSPI 또는 종목 코드 6자리). "
            "**대상 목록 밖은 거절된다** — 크기 앵커는 추론 대상에만 뜻이 있다."
        )
    )


class DailyHistoryArgs(ToolArgs):
    symbol: str = Field(
        description=(
            "일봉을 볼 심볼 하나. macro_changes가 돌려준 symbol 값(예: SP500_FUT, USDKRW, VIX), "
            "국내 지수(KOSPI, KOSDAQ), 추적 종목 코드 6자리(예: 005930)를 쓸 수 있다. "
            "없는 심볼을 물으면 쓸 수 있는 목록을 돌려준다."
        )
    )
    days: int = Field(
        default=10,
        description=f"최근 며칠치를 볼지. {MIN_HISTORY_DAYS}~{MAX_HISTORY_DAYS}.",
    )


class AnalystOpinionsArgs(ToolArgs):
    ticker: str = Field(
        description="추적 종목 코드 6자리(예: 005930). 추적 목록 밖이면 거절하고 쓸 수 있는 목록을 돌려준다."
    )


class EventSurprisesArgs(ToolArgs):
    ticker: str = Field(
        description="추적 종목 코드 6자리(예: 005930). 추적 목록 밖이면 거절하고 쓸 수 있는 목록을 돌려준다."
    )


TOOL_DESCRIPTIONS: dict[str, str] = {
    "recent_documents": (
        "최근 평가된 경제 문서 중 가치 점수가 높은 것들. 제목, 발행 시각, 방향, 점수, "
        "관련 종목 티커, 그리고 앞선 평가가 남긴 새 사실과 판단 근거를 준다. "
        "source_slug가 naver_research_로 시작하면 증권사 리서치 리포트다 — 제목 끝에 증권사 이름이 있고, "
        "종목분석은 요약 첫머리에 투자의견·목표가가 있다."
    ),
    "recent_disclosures": "추적 종목에 대해 최근 접수된 DART 공시. 회사명, 보고서명, 접수일, 감지 시각을 준다.",
    "macro_changes": (
        "분석 창 동안 해외 지수·선물·환율·금리·채권선물·원자재·암호화폐가 얼마나 움직였나. "
        "**축이 둘이다** — `change_pct`는 창 첫 봉 대비이고 `prev_close_change_pct`는 "
        "직전 정규장 종가 대비다. 네 예측의 기준가와 채점 축은 전일 종가이므로 하루 등락으로 "
        "읽을 값은 뒤쪽이다. 금리 계열은 퍼센트가 아니라 bp 차이로 준다(`*_bp`). "
        "전일 종가가 봉에 없는 심볼은 뒤쪽 칸이 통째로 빠진다 — 0이라는 뜻이 아니다. "
        "**국내 지수(코스피·코스닥)는 여기 안 나온다** — 창이 당일 09:00부터라 개장 갭이 빠져 "
        "값이 하루 등락과 어긋난다. 국내 지수는 관측 상태가 전일 종가 기준으로 이미 준다. "
        "**밤사이 미국장이 얼마나 움직였나는 us_market_close로 본다** — 이 툴의 창 변화는 창 첫 봉 "
        "대비라 마감 직전 몇 시간만 쌓이는 현물 지수는 거의 0으로 보인다."
    ),
    "us_market_close": (
        "밤사이 미국장 마감. 미국 지수·선물·원자재·환율·금리의 마감 종가와 **전일 정규장 종가 대비** "
        "등락을 준다(금리 계열은 퍼센트가 아니라 bp). 한국 장이 열리기 전 가장 먼저 볼 값이다. "
        "빈 배열은 이 창에 미국 봉이 없다는 뜻이지 움직이지 않았다는 뜻이 아니다 — 장후 슬롯의 창은 "
        "당일 09:00부터라 미국 세션이 창 밖이다."
    ),
    "past_theses": (
        "이 대상에 대해 전에 낸 추론과 그 결과. 그때의 세 확률·세 이유, 지평별 실제 등락률과 "
        "Brier 점수, 사후 해설과 판정을 준다. 같은 실수를 반복하고 있는지 볼 수 있다. "
        "`run_slot`이 `pre_open`이면 그날 장 열리기 전의 예측이라 채점이 붙고, `post_close`면 "
        "장이 닫힌 뒤 '왜 그렇게 움직였나'를 적은 해석이라 채점 없이 해설·판정만 붙는다. "
        "슬롯마다 최근 n건씩 준다."
    ),
    "macro_indicators": (
        "각국 국채 금리 곡선과 물가·실물 지표의 최신 관측값, 그리고 직전 값 대비 변화. "
        "미국·한국·일본·영국·독일·유로 지역 등의 만기별 금리를 만기와 나라와 함께 준다. "
        "금리 변화는 퍼센트가 아니라 bp다. 시세(macro_changes)로는 안 보이는 채권 시장을 본다."
    ),
    "market_investor_flows": (
        "코스피·코스닥의 외국인·기관·개인 장중 누적 순매수. 지수가 왜 그렇게 움직였는지를 "
        "누가 샀고 누가 팔았나로 본다. 금액 단위는 백만원이다."
    ),
    "market_breadth": (
        "코스피·코스닥의 상승·보합·하락 종목 수와 상한가·하한가 수. 지수 등락률만으로는 "
        "안 보이는 것을 본다 — 지수는 올랐는데 하락 종목이 더 많은 날이 있다."
    ),
    "stock_investor_flows": (
        "추적 종목의 최근 확정 수급(외국인·기관·개인 순매수)과 오늘의 장중 추정치. "
        "확정은 마감 뒤 값이고 추정은 장중 값이라 따로 표시해 준다."
    ),
    "market_funds": (
        "고객예탁금, 신용융자 잔고, 미수금 등 국내 증시자금의 최근 추이. 살 돈이 늘고 있는지 줄고 있는지를 본다."
    ),
    "daily_history": (
        "심볼 하나의 최근 일봉(시가·고가·저가·종가·거래량)과 그 심볼의 기술적 보조지표. "
        "macro_changes가 창 하나의 양 끝만 주는 것과 달리 며칠치 추세를 준다 — "
        "'어제 하루 빠진 것'과 '닷새째 빠지는 중'을 가른다. "
        "technical_snapshot은 마지막 확정 일봉 기준의 SMA20·SMA60·RSI14·MACD(라인·시그널·히스토그램)와 "
        "직전 20거래일 평균 대비 거래량 비율이다. as_of_date가 그 기준일이고, 표본이 60봉에 못 미치거나 "
        "가격이 하루에 35퍼센트 넘게 튄 구간이 있으면 null이다 — 0으로 채우지 않는다."
    ),
    "typical_move": (
        "이 대상이 최근 하루에 실제로 얼마나 움직였나. **크기(up_return_pct·down_return_pct)를 "
        "쓰기 전에 부르는 기준선이다.** 오른 날의 등락 중앙값과 내린 날의 등락 중앙값을 나눠 주고 "
        "|등락|의 p25·중앙값·p75·p90도 함께 준다 — 조건부 크기의 짝이다. 창 둘(최근 20거래일과 "
        "250거래일)을 나란히 줘서 지금이 평소보다 큰 구간인지 읽는다. "
        "sample_size가 모자라면 통계가 null이다 — **재지 않았다는 뜻이지 0이 아니다.** "
        "**장중 잔여 구간(지금 가격에서 마감까지)의 분포는 주지 않는다** — 분봉 이력이 짧아 셀 "
        "표본이 없다. 값은 하루 전체 등락이고 장중 슬롯은 남은 시간만큼 줄여 쓴다."
    ),
    "short_and_credit": (
        "추적 종목의 최신 공매도 수량·비중, 대차 잔고, 신용융자 잔고. 셋이 서로의 재고라 "
        "한 표로 준다. 수집을 최근에 시작해 아직 며칠치뿐일 수 있다."
    ),
    "analyst_opinions": (
        "추적 종목 하나에 대한 증권사 애널리스트의 최근 투자의견·목표주가. 발표일, 증권사, 의견, "
        "직전 의견, 목표가, 발표 전일 종가, 목표가 괴리율을 최신 발표부터 준다. 의견이 바뀌었는지는 "
        "의견과 직전 의견을 비교해 읽는다. 같은 증권사가 같은 날 낸 리포트가 수집돼 있으면 그 요약이 "
        "reason에 함께 온다 — 왜 그 목표가인지가 거기 있다. 인용할 ref가 붙은 리포트 전문은 "
        "recent_documents가 naver_research_* 문서로 준다."
    ),
    "event_surprises": (
        "추적 종목 하나의 이벤트가 시장 기대에 부합했나. 둘을 준다. "
        "outcomes는 이미 발표된 이벤트의 판정이다 — 기대치, 실제 발표값, 어긋난 정도(퍼센트), "
        "beat(상회)/meet(부합)/miss(미달), 발표 시각. **주가는 절대 수치가 아니라 기대 대비로 "
        "움직인다** — 좋은 실적도 기대에 못 미치면 떨어진다. "
        "pending_expectations는 아직 발표되지 않은 이벤트의 대표 기대치다. 오늘 발표가 나오면 "
        "그 숫자가 기준선이다. 금액은 전부 원 단위다."
    ),
}
