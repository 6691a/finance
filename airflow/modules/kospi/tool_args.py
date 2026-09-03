"""툴 인자 스키마와 설명. **JSON Schema는 여기서 뽑힌다.**

`StructuredTool.from_function(args_schema=...)`이 이 모델들에서 스키마를 만든다. wire format
dict(`{"type": "function", ...}`)를 손으로 쓰지 않는다 — 그건 제공처 규격이라 이름·타입이
실제 함수와 어긋나도 아무도 못 잡는다.

**상한 값은 코드 상수를 f-string으로 싣는다.** 숫자를 두 곳에 적으면 반드시 어긋난다.
상수는 `kospi.domain`에 있다.

설명을 별도 파일로 빼지 않는 이유는 상한 값이 `Field(description=...)`에 실려 Pydantic 모델
선언과 한 몸이기 때문이다. 떼면 스키마와 설명이 두 파일로 갈린다.
"""

from typing import Annotated

from langchain_core.tools import InjectedToolCallId
from pydantic import BaseModel, ConfigDict, Field

from modules.kospi.domain import (
    DEFAULT_HISTORY_DAYS,
    DEFAULT_WINDOW_HOURS,
    HISTORY_FACTORS,
    MAX_HISTORY_DAYS,
    MAX_TOOL_CALLS,
    MAX_WINDOW_HOURS,
    MIN_HISTORY_DAYS,
    MIN_WINDOW_HOURS,
    factor_label,
)

# 모델이 고를 수 있는 요인 목록을 설명에 그대로 싣는다. 목록 밖 값은 툴이 거절한다.
FACTOR_CHOICES = ", ".join(f"{code.value}({factor_label(code)})" for code in HISTORY_FACTORS)


class _Args(BaseModel):
    """인자 모델은 전부 불변이고 모르는 키를 거절한다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # **모델에게 보이지 않는 칸이다.** `InjectedToolCallId`가 붙으면 LangChain이
    # `tool_call_schema`에서 빼고 실행 시점에 진짜 call id로 채운다. 이 한 칸이 있어야
    # 원장 래퍼(`tool_ledger.record`)가 "지금 부른 것이 어느 요청이었나"를 안다.
    #
    # **이 칸이 없으면 원장이 조용히 거짓말을 한다**(2026-09-03 실측, 백테스트 69회 전부).
    # 래퍼가 기록을 못 찾아 결과를 못 채우고, `finish_round`가 "결과 없음 = 함수에 못 닿음"
    # 으로 읽어 `validation` 오류로 분류한다. 실제 결과 JSON은 `error` 칸에 들어간다.
    # 툴은 정상이고 모델도 답을 받으므로 태스크는 성공이다 — 원장만 틀린다.
    tool_call_id: Annotated[str, InjectedToolCallId] = ""


class FactorHistoryArgs(_Args):
    factor: str = Field(description=f"조회할 요인. 하나만 고른다. 쓸 수 있는 값: {FACTOR_CHOICES}")
    days: int = Field(
        default=DEFAULT_HISTORY_DAYS,
        ge=MIN_HISTORY_DAYS,
        le=MAX_HISTORY_DAYS,
        description=(
            f"거슬러 볼 관측 수. {MIN_HISTORY_DAYS}~{MAX_HISTORY_DAYS}. "
            "영업일이 아니라 저장된 행 수다 — 휴장이 끼면 그만큼 앞까지 본다"
        ),
    )


class RecentNewsArgs(_Args):
    hours: int = Field(
        default=DEFAULT_WINDOW_HOURS,
        ge=MIN_WINDOW_HOURS,
        le=MAX_WINDOW_HOURS,
        description=(
            f"기준 시각에서 거슬러 올라갈 시간. {MIN_WINDOW_HOURS}~{MAX_WINDOW_HOURS}. "
            "지금이 아니라 이 실행의 기준 시각이 창의 끝이다"
        ),
    )


class RecentDisclosuresArgs(_Args):
    hours: int = Field(
        default=DEFAULT_WINDOW_HOURS,
        ge=MIN_WINDOW_HOURS,
        le=MAX_WINDOW_HOURS,
        description=f"기준 시각에서 거슬러 올라갈 시간. {MIN_WINDOW_HOURS}~{MAX_WINDOW_HOURS}",
    )


TOOL_DESCRIPTIONS: dict[str, str] = {
    "factor_history": (
        "요인 하나의 일별 값과 직전 관측 대비 변화를 준다. "
        "관계 표에 가중치가 있는 요인이 오늘 실제로 어디로 갔는지를 확인하는 자리다. "
        f"금리 요인의 변화는 bp이고 나머지는 값과 퍼센트다. 이 실행에서 툴은 모두 합쳐 "
        f"{MAX_TOOL_CALLS}번까지 부를 수 있다."
    ),
    "recent_news": (
        "기준 시각까지 들어온 평가된 기사. 제목·발행 시각·방향·가치 점수·평가 요약·종목 태그를 준다. "
        "본문은 없다 — 요약에 적힌 숫자를 그대로 옮겨 쓰지 마라. 숫자는 factor_history가 갖고 있다."
    ),
    "recent_disclosures": (
        "기준 시각까지 접수된 DART 공시 중 **본문이 있는 것**. 회사명·보고서명·접수 시각과 본문 앞부분을 준다. "
        "**수집 범위는 삼성전자·SK하이닉스 둘뿐이고** 본문은 주요사항·자기주식·배당·실적·조회공시 같은 "
        "사건성 종류에만 있다 — 시장 전체 공시가 아니며 빈 결과가 보통이다. "
        "본문은 접수된 원문이라 거기 적힌 금액과 날짜는 그대로 인용해도 된다. "
        "다만 원문에 없는 것을 보고서명에서 짐작하지 마라."
    ),
}
