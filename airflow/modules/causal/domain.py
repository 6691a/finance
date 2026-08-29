"""주간 인과 그래프의 어휘와 셈 — 상수, 주 경계, 입력 해시.

**이 모듈은 LangChain·LangGraph·Airflow를 import하지 않는다.** DAG 테스트와 순수 함수
테스트가 그 무게 없이 돌아야 하고, `tests/modules/test_import_weight.py`가 그 경계를 잰다.
무거운 것은 `causal.generation`에 있다.

계약은 `docs/analysis/market-causal-graph.md`다.
"""

import hashlib
import re
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from modules.utility import KST_TIMEZONE

# 프롬프트 판. `modules/prompts/causal_graph.yaml`의 문장을 고치면 이 값을 올리고
# `tests/modules/test_prompt_versions.py`의 해시를 같은 커밋에서 갱신한다.
#
# 판 2는 문장이 아니라 **자리표시자에 들어가는 값의 모양**이 바뀐 것이다(2026-08-28).
# 근거 후보를 대상 코드로 안 좁히게 되면서 문서 줄이 `(대상, 시각, score…)`에서
# `(태그 목록, 시각, score…)`으로 바뀌었다. 모델이 보는 입력이 달라지므로 판을 가르지만
# YAML은 그대로라 해시가 1과 같다.
#
# 판 3은 어휘 재사용에 조건을 달았다(2026-08-28). 8/03을 씨앗으로 두 주를 돌리자 채널이
# 6개로 굳었는데, 그 대가로 사슬이 `reasoning`을 배신했다 — "반도체 호황이 이익 기대에
# 반영됐다"고 쓰면서 사슬은 `위험선호 > 금리 기대 > 밸류에이션`이었다. 목록에 `이익 기대`가
# 없어 있는 것에 욱여넣은 것이다. 판정 기준(사슬과 `reasoning`이 같은 말을 하는가)을
# 프롬프트에 싣는다.
#
# 판 4는 사건을 고르는 규칙을 더했다(2026-08-28). 판 3이 채널 어휘를 고쳤더니 남은 결함이
# 사건 쪽이었다 — 지난주 사건이 이번 주 경로 넷을 먹었고, 같은 일이 날짜만 달리해 노드 둘로
# 갈렸다. 후보 창을 `EVENT_LOOKBACK_WEEKS`로 좁히는 것과 함께 간다.
#
# 판 5는 `confidence`를 가르는 기준을 밝혔다(2026-08-28). 판 4 실행이 경로 서른넷을 전부
# `plausible`로 냈다. 정의가 "같은 기간에 함께 관찰됨"이라 등락만 봐도 참으로 읽혔고,
# "확실하지 않으면 `plausible`"이 기본값을 눌러 두고 있었다. 가르는 것은 확신의 세기가
# 아니라 근거가 그 방향을 말했는가 하나다.
#
# 판 6은 툴 셋을 붙였다(2026-08-28). 다섯 판을 돌린 결과가 늘릴 신호를 냈다 — `observed`가
# 5/25에 그치고, `외국인 수급` 채널을 쓰면서 투자자별 매매를 한 줄도 안 봤다. 프롬프트가
# 언제 무엇을 부를지 안내한다(설계 §5.2).
#
# 판 7은 넷째 툴 `macro_indicators`를 안내한다(2026-08-28). 수집 중인 지표 106계열 중 이
# 그래프가 값으로 보던 것이 대상 둘뿐이었다 — 나머지는 기사 문장으로만 스쳐 갔다.
#
# 판 8은 기사 숫자를 근거로 쓰지 못하게 막았다(2026-08-28). 판 7 프로토타입이 CPI 지수를
# 툴로 받고도 `연율 3.4퍼센트`를 **기사 요약에서** 가져다 `reasoning`에 적었다. 문서는
# 요약만 있고 원문(`document.body`)이 전 건 비어 있어 그 숫자를 되짚을 방법이 없다.
#
# 판 9는 공시가 예외임을 밝힌다(2026-08-29). 후보 줄에 공시 원문 본문이 붙으면서 판 8의
# "숫자를 옮기지 마라"가 공시까지 덮어 버렸다. 공시 본문은 요약이 아니라 접수된 원문이다.
PROMPT_VERSION = "9"

# 대상 주 `W`와 실행 주 `W+2`의 거리. 설계 §2.
RUN_LAG_WEEKS = 2

# 한 경로가 거치는 전달 단계의 상한. `apps/models/analysis/causal.py`의 같은 이름 상수와
# 값이 같다. **도메인에서 온 값이다** — 통화정책 전달경로 서술이 대체로
# `정책금리 → 시장금리 → 자산가격·신용 → 실물·물가` 3~4단이다. 이 값을 올려 그래프를 깊게
# 만들려 하지 않는다. 깊이는 주가 쌓이며 노드를 공유하는 데서 오고, 상한을 올리면 한 응답
# 안에서 모델이 단계를 지어내기 시작한다.
MAX_CHAIN = 3

# 한 실행이 낼 수 있는 경로 수. 8주 프로토타입은 주당 8~28개를 냈다.
MAX_PATHS = 40

# 경로 설명 한 문장의 길이 상한.
MAX_REASONING_CHARS = 200

# 조사 왕복 상한(2026-08-28에 툴을 붙이며 생겼다). 대상 열에 툴 셋이면 한 왕복에 열 번쯤
# 부르므로 셋이면 충분하고, 넘으면 `answer`로 넘어가 가진 것으로 답한다. `thesis`가 5인 것은
# 그쪽 툴이 14개이고 슬롯마다 앞단이 달라서다.
MAX_TOOL_ROUNDS = 3

# 사건 후보를 몇 주까지 거슬러 보여 주는가. 사건은 수렴하지 않으므로 날짜로 좁힌다(설계 §4).
#
# **4에서 1로 좁혔다**(2026-08-28). 채널과 달리 **사건은 재사용이 미덕이 아니다** — 날짜가
# 붙은 일회성이라, 지난달 사건이 이번 주 등락을 만들었다고 적히면 그건 대개 틀린 것이다.
# 4주로 두자 `미국 고용 둔화 확인`(8/07)이 경로 열둘에 쓰였고 그중 넷이 8/10 주 것이었다.
# 그 주에 CPI라는 자기 사건이 있는데도 지난주 것을 끌어왔다.
#
# **0이 아니라 1인 이유**는 주 경계에 걸린 반응이 실재하기 때문이다. 금요일 밤 미국 지표는
# 그 주 사건이지만 다음 주 월요일 국내 시장이 처음 반응한다.
EVENT_LOOKBACK_WEEKS = 1

# event-time cutoff의 시각(KST). KRX 정규장 종가가 확정된 뒤다.
#
# **`W+1` 금요일이 KRX 휴장이어도 앞으로 당기지 않는다.** cutoff는 "이 시각 이후 감지된
# 행을 뺀다"라서, 휴장이면 그 앞 거래일까지의 값이 전부 이 시각 안에 들어와 있다. 반대로
# 마지막 거래일로 당기면 그 뒤에 들어온 문서를 잃는다.
CUTOFF_TIME_KST = time(15, 40)

# `date.fromisoformat`은 `2026-W28` 같은 ISO 주 표기도 받아 그 주의 월요일로 바꾼다.
# 조용히 통과하면 어느 표기로 준 실행인지 못 가르므로 달력 하루만 받는다.
CALENDAR_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_week(logical_date: datetime, param: str | None) -> date:
    """이 실행이 다룰 대상 주 `W`의 월요일(KST).

    `logical_date`는 UTC aware이고 스케줄이 UTC 일 22:00이라, **KST로 바꾸지 않고 요일을
    보면 한 주가 밀린다.**
    """
    if param:
        if not CALENDAR_DATE_PATTERN.match(param):
            raise ValueError(f"week_start must be YYYY-MM-DD, got {param!r}")
        week_start = date.fromisoformat(param)
        if week_start.weekday() != 0:
            raise ValueError(f"week_start must be a Monday, got {param!r}")
        return week_start
    kst_day = logical_date.astimezone(KST_TIMEZONE).date()
    run_monday = kst_day - timedelta(days=kst_day.weekday())
    return run_monday - timedelta(weeks=RUN_LAG_WEEKS)


class StoreOutcome(BaseModel):
    """저장이 무엇을 했는지. **새 채널 수가 어휘 수렴의 유일한 관측이다**(2026-08-28).

    새 이름에 상한을 두지 않기로 하면서 그 자리를 이 값이 받았다. 매주 늘기만 하면
    정규화가 안 되고 있는 것이고, 그때 좁힐 자리는 상한이 아니라 프롬프트다.
    """

    model_config = ConfigDict(frozen=True)

    stored: int
    """실제로 들어간 경로 수. 자연키 충돌로 빠진 것은 세지 않는다."""
    new_channels: int
    """이 실행이 새로 만든 채널 이름 수."""


class CausalWindow(BaseModel):
    """한 실행이 보는 구간. 값이 재시도 사이에 바뀌면 원본과 저장값이 어긋난다."""

    model_config = ConfigDict(frozen=True)

    week_start: date
    """대상 주 `W`의 월요일(KST). 자연키의 축이다."""
    week_end: date
    """`W`의 금요일. 사건이 일어난 창의 끝이다."""
    reaction_end: date
    """`W+1`의 금요일. 반응(T+5)이 확정되는 날이다."""
    as_of_at: datetime
    """event-time cutoff(UTC). 이 시각 이후 감지·평가·갱신된 행은 보지 않는다."""


def window_for(week_start: date) -> CausalWindow:
    """대상 주에서 조회 창을 만든다.

    `week_end`·`reaction_end`는 달력 금요일이다. 휴장 판정을 하지 않는 이유는
    `CUTOFF_TIME_KST` 주석에 있다 — 이 모듈이 DB를 보지 않는 근거이기도 하다.
    """
    week_end = week_start + timedelta(days=4)
    reaction_end = week_end + timedelta(days=7)
    as_of_kst = datetime.combine(reaction_end, CUTOFF_TIME_KST, tzinfo=KST_TIMEZONE)
    return CausalWindow(
        week_start=week_start,
        week_end=week_end,
        reaction_end=reaction_end,
        as_of_at=as_of_kst.astimezone(UTC),
    )


def input_hash(
    *,
    week_start: date,
    target_codes: Iterable[str],
    candidate_refs: Iterable[str],
) -> str:
    """이 실행의 입력을 한 값으로 접는다. 판정이 아니라 감사 값이다(설계 §5.4).

    **정렬한다.** 후보 조립 SQL의 반환 순서가 바뀌어도 같은 입력이면 같은 해시여야 한다.
    실행 시각과 `dag_run_id`는 넣지 않는다 — 넣으면 매번 달라져 뜻이 사라진다. 툴 호출도
    넣지 않는다. 재현의 앵커는 후보이고 툴은 `thesis_tool_call` 원장이 추적한다.
    """
    material = "|".join(
        (
            week_start.isoformat(),
            ",".join(sorted(target_codes)),
            ",".join(sorted(candidate_refs)),
            PROMPT_VERSION,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


class CausalTargetKind(StrEnum):
    """대상이 어느 마스터에서 오는지. **값의 성격이 아니라 저장소를 가른다.**

    값은 `apps/models/analysis/causal.py`의 같은 이름 enum과 같아야 한다. Airflow는 `apps/`를
    보지 못해 import하지 못하므로 한 벌 더 둔다(중복 허용 + 테스트 대조 규칙).
    """

    INSTRUMENT = "instrument"
    INDEX = "index"
    QUOTE = "quote"
    INDICATOR = "indicator"


class CausalSign(StrEnum):
    """이 경로가 대상을 어느 쪽으로 밀었다고 모델이 주장하는가."""

    UP = "up"
    DOWN = "down"


class CausalConfidence(StrEnum):
    """주장의 성격. 둘 다 인과의 증명이 아니다."""

    OBSERVED = "observed"
    PLAUSIBLE = "plausible"


class CausalReturnUnit(StrEnum):
    """실현 등락의 단위. 가격은 percent, 금리는 basis_point다.

    `KTB10Y` 4.239 → 4.313은 +1.75%가 아니라 +7.4bp다. 한 칸에 섞으면 KOSPI의 +10.77%와
    크기를 비교할 수 없게 된다.
    """

    PERCENT = "percent"
    BASIS_POINT = "basis_point"


class CausalTarget(BaseModel):
    """경로가 닿는 대상 하나."""

    model_config = ConfigDict(frozen=True)

    kind: CausalTargetKind
    code: str
    provider: str | None = None
    """`indicator` 종류만 채운다. `indicator_observation`은 `(provider, series_id)`가 키라
    `series_id` 하나로 거는 쿼리는 제공처가 늘어나면 조용히 틀린다(저장소 규칙). 나머지
    종류는 제공처를 자기 마스터가 알아서 여기 안 담는다."""


# 국내 지수. 종목과 달리 마스터를 훑지 않는다 — 늘어나는 목록이 아니다.
INDEX_TARGETS: tuple[CausalTarget, ...] = (
    CausalTarget(kind=CausalTargetKind.INDEX, code="KOSPI"),
    CausalTarget(kind=CausalTargetKind.INDEX, code="KOSDAQ"),
)

# 매크로 다섯. **이것들이 대상에 있어야 그래프가 깊어진다**(설계 §3.1.1) — 대상이 못 되면
# `미국 10년물 국채금리 상승` 같은 값이 사건으로만 들어와 사슬이 거기서 끊긴다.
MACRO_TARGETS: tuple[CausalTarget, ...] = (
    CausalTarget(kind=CausalTargetKind.QUOTE, code="USDKRW"),
    CausalTarget(kind=CausalTargetKind.QUOTE, code="US10Y"),
    CausalTarget(kind=CausalTargetKind.QUOTE, code="SOX"),
    CausalTarget(kind=CausalTargetKind.QUOTE, code="VIX"),
    CausalTarget(kind=CausalTargetKind.QUOTE, code="NASDAQ100_FUT"),
)

# 금리 둘. **정책금리는 사건이자 대상이다** — 한은 인상이 사건이면 KTB10Y가 대상이고,
# 국채 급등이 사건이면 KOSPI가 대상이다. 그 겹침이 그래프를 깊게 만든다(설계 §9.3 ①).
#
# 값이 있는 구간이 아직 짧다(정책금리 2026-07-14~, 국채 2026-08-10~). 값이 없는 주는 그
# 대상만 저장되지 않고 나머지는 그대로 돈다(설계 §6).
INDICATOR_TARGETS: tuple[CausalTarget, ...] = (
    CausalTarget(kind=CausalTargetKind.INDICATOR, code="KRBASE", provider="ecos"),
    CausalTarget(kind=CausalTargetKind.INDICATOR, code="KTB10Y", provider="ecos"),
)


class TargetReturns(BaseModel):
    """대상 하나의 실현 등락. **SQL이 계산한 값이고 모델이 만들지 않는다.**

    셋이 모두 있어야 저장된다(설계 §6). 하나라도 없으면 그 대상이 통째로 빠진다 —
    NULL로 저장하면 "안 쟀다"와 "잴 수 없었다"가 나중에 구분되지 않는다.
    """

    model_config = ConfigDict(frozen=True)

    week: float
    """대상 주 안의 변화."""
    t1: float
    """주 종료 다음 KRX 거래일까지의 변화."""
    t5: float
    """주 종료 +5 KRX 거래일까지의 변화."""
    unit: CausalReturnUnit
    """가격·지수·환율은 percent, 금리는 basis_point다."""


class DocumentCandidate(BaseModel):
    """프롬프트에 실리는 문서 하나."""

    model_config = ConfigDict(frozen=True)

    ref: str
    tags: tuple[str, ...] = ()
    """이 문서에 붙은 종목·지표 태그 전부. **대상 목록 밖 값도 그대로 싣는다** —
    어느 대상에 닿았는지는 모델이 판단한다. 태그가 없는 문서(시황·경제)도 후보가 된다."""
    title: str
    summary: str
    source_slug: str
    published_at: datetime
    value_score: int
    assessed_direction: str | None


class DisclosureCandidate(BaseModel):
    """프롬프트에 실리는 공시 하나."""

    model_config = ConfigDict(frozen=True)

    ref: str
    target_code: str
    company_name: str
    report_name: str
    receipt_date: date
    body: str
    """원문에서 태그를 걷어낸 본문. **비어 있을 수 없다** — 보고서명 한 줄로는 모델이 내용을
    지어내는 것 말고 할 수 있는 일이 없어, 본문이 없는 공시는 조회가 아예 빼고 온다."""


class SignalCandidate(BaseModel):
    """프롬프트에 실리는 기술적 신호 하나. 지표값이 아니라 사건이라 인용할 수 있다."""

    model_config = ConfigDict(frozen=True)

    ref: str
    target_code: str
    signal_date: date
    kind: str
    direction: str


class CandidateSet(BaseModel):
    """한 실행이 프롬프트에 싣는 근거 후보 전부.

    **`refs`가 레지스트리다.** 모델이 인용한 `evidence_refs`를 이 목록으로 검증하고 목록 밖
    값은 버린다. 그것이 모델이 근거를 지어내지 못하게 막는 유일한 장치다.
    """

    model_config = ConfigDict(frozen=True)

    documents: tuple[DocumentCandidate, ...] = ()
    disclosures: tuple[DisclosureCandidate, ...] = ()
    signals: tuple[SignalCandidate, ...] = ()

    @property
    def refs(self) -> tuple[str, ...]:
        """후보 ref 전부. **정렬한다** — `input_hash`가 이것을 접으므로 조회 순서가 흔들려도
        같은 입력이면 같은 해시여야 한다."""
        return tuple(
            sorted(
                [item.ref for item in self.documents]
                + [item.ref for item in self.disclosures]
                + [item.ref for item in self.signals]
            )
        )


class EventOption(BaseModel):
    """프롬프트에 후보로 실리는 기존 사건 하나."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    """`e:<id>` 꼴. 모델은 이 값으로 고른다 — 이름만 주면 같은 것을 다시 만든다."""
    title: str
    occurred_on: date


class ChannelOption(BaseModel):
    """프롬프트에 후보로 실리는 기존 경로 하나."""

    model_config = ConfigDict(frozen=True)

    node_id: str
    """`c:<id>` 꼴."""
    name: str


class NodeChoice(BaseModel):
    """사건이나 경로 한 칸. 기존 것을 고르거나 새로 만든다.

    **이 한 칸이 §4 전체를 담는다.** 모델이 후보 목록에서 고르면 `existing_id`, 없으면
    `new_name`이고, 그 선택이 서로 다른 주의 그래프를 잇거나 새 노드를 만든다.

    LLM 응답 스키마의 일부지만 여기 둔다 — `store.py`가 이것을 읽어야 하는데, generation에
    두면 저장 층이 LangChain을 끌고 와 DAG 파일이 그 무게를 문다.
    """

    existing_id: str = Field(
        default="",
        description="후보 목록에 있는 id(e:12 또는 c:3). 새로 만들면 빈 문자열",
    )
    new_name: str = Field(
        default="",
        description="새로 만들 이름. 기존을 고르면 빈 문자열",
    )

    @property
    def is_new(self) -> bool:
        return not self.existing_id and bool(self.new_name)


class VerifiedPath(BaseModel):
    """검증을 마친 경로 하나. 저장 코드는 이것만 본다."""

    model_config = ConfigDict(frozen=True)

    event: NodeChoice
    event_date: str
    channels: tuple[NodeChoice, ...]
    target_kind: str
    target_code: str
    sign: str
    confidence: str
    reasoning: str
    evidence_refs: tuple[str, ...]
