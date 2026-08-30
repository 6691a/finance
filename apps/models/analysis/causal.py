"""주간 사후 인과 그래프 — 사건, 전달 경로, 그 둘을 대상에 잇는 경로와 단계.

설계는 `docs/analysis/market-causal-graph.md`다.

**`thesis`와 목적이 다르다.** 저쪽은 슬롯마다 확률을 남기고 채점받는 예측이고, 여기는 한 주가
끝난 뒤 "무엇이 어떤 경로로 무엇에 닿았나"를 노드·엣지로 쪼개 **누적**한다. 다중 홉은 한 번의
분석 안에서 생기지 않고 주들이 같은 노드를 공유하면서 생긴다 — 그래서 마스터 둘(`market_event`·
`market_channel`)의 자연키가 이 설계에서 가장 중요한 장치다.

vocabulary는 Airflow의 `modules/causal/domain.py`에 값이 한 벌 더 있다. Airflow는 `apps/`를
보지 못해 import하지 못하므로 중복을 허용하고 `tests/models/test_analysis_models.py`가 대조한다.
"""

from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import EntityBase, table_options
from apps.models.analysis._columns import _enum_column

# 한 경로가 거치는 전달 단계의 상한. `modules/causal/domain.py`의 같은 이름 상수와 값이 같다.
MAX_CHAIN = 3


class CausalSign(StrEnum):
    """이 경로가 대상을 어느 쪽으로 밀었다고 모델이 주장하는가."""

    UP = "up"
    DOWN = "down"


class CausalConfidence(StrEnum):
    """주장의 성격. **셋 다 인과의 증명이 아니다.**

    `MarketEpisode` 설계의 "검증 없이 원인이라 단정하지 않는다"를 엣지 속성으로 옮긴 것이다.

    **가르는 것은 확신의 세기가 아니라 무엇이 뒷받침하는가다.** 사다리가 한 줄로 읽힌다 —
    문서가 말했다 > 값이 그렇게 움직였다 > 우리가 이었다. 조심스럽다는 이유로 근거가 말한
    것을 낮추지 않고, 근거가 없는데 올리지도 않는다.

    **칸을 나눈 이유가 있다**(설계 §11.3). 하나로 뭉치면 조회가 "근거 문서가 말한 것"과
    "가격이 그렇게 보인 것"을 영영 못 가르고, 비율을 지표로 삼는 순간 정의를 느슨하게 하는
    것이 가장 싼 개선책이 된다.
    """

    OBSERVED = "observed"
    """근거 후보 중 하나가 그 방향을 직접 말했다. 그 ref가 `MarketCausalEvidence`에 있다."""
    ENDPOINT_OBSERVED = "endpoint_observed"
    """원인과 결과가 둘 다 우리가 가진 값이고 방향·선후가 주장과 맞다. **양 끝을 본 것이지
    사이를 본 것이 아니다.** 대상에서 출발한 경로만 이 값을 가질 수 있다 — 사건에서
    출발하면 원인 쪽이 문서라 값으로 대조할 것이 없다."""
    PLAUSIBLE = "plausible"
    """메커니즘은 그럴듯하지만 값도 문서도 그 말을 하지 않았다. 모델이 이은 것이다."""


class CausalReturnUnit(StrEnum):
    """실현 등락의 단위. **가격과 금리를 한 단위로 못 담는다.**

    `KTB10Y`가 4.239에서 4.313으로 가는 것은 +1.75%가 아니라 +7.4bp다. 퍼센트로 저장하면
    KOSPI의 +10.77%와 한 칸에 들어가 크기 비교가 조용히 무의미해지고, 퍼센트에서 bp를
    역산할 수도 없어(원값이 없으면) 표시 층에 미룰 수도 없다. 그래서 저장할 때 정한다.
    """

    PERCENT = "percent"
    """가격·지수·환율. 종가 대비 변화율."""
    BASIS_POINT = "basis_point"
    """금리. 값의 차이에 100을 곱한 bp다."""


class CausalTargetKind(StrEnum):
    """대상이 어느 마스터에서 오는지. **값의 성격이 아니라 저장소를 가른다.**

    `US10Y`는 `quote_daily`에 있고 `KTB10Y`는 `indicator_observation`에 있다 — 둘 다 국채
    금리인데 수집 경로가 달라 자리가 다르다. 검증하는 쪽이 어느 마스터를 볼지 정해야 하므로
    그 사실을 이 칸이 들고 있는다.
    """

    INSTRUMENT = "instrument"
    """개별 종목. `instrument` 마스터."""
    INDEX = "index"
    """국내 지수. `quote_symbol` 마스터."""
    QUOTE = "quote"
    """해외 지수·환율·선물. `quote_symbol` 마스터."""
    INDICATOR = "indicator"
    """지표 시계열. `indicator_series` 마스터."""


class MarketEvent(EntityBase):
    """그 주에 실제로 일어난 일 하나. 그래프의 출발 노드다.

    **자연키에 날짜가 들어간다.** 같은 제목이 다른 날 다시 일어나면 다른 사건이기 때문이다 —
    `미국 반도체 지수 하락`은 8주 프로토타입에서 두 번 나왔고 서로 다른 사건이었다. 반대로
    같은 날 같은 제목이면 같은 사건이라 `ON CONFLICT DO NOTHING`으로 합친다.

    어휘는 LLM이 자유롭게 만들고, 다음 주 프롬프트가 최근 몇 주의 사건을 후보로 받아 고른다.
    """

    __tablename__ = "market_event"
    __table_args__ = (
        UniqueConstraint("title", "occurred_on", name="uq_market_event_natural_key"),
        table_options(
            comment="주간 인과 그래프의 사건 노드. 제목과 발생일이 자연키다",
            database="default",
        ),
    )

    title: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="사건 한 줄. 예: 한국은행 기준금리 25bp 인상. LLM이 만든 자유 텍스트다",
    )
    occurred_on: Mapped[date] = mapped_column(
        nullable=False,
        comment=(
            "사건이 일어난 날(KST 달력일). 프롬프트 후보를 최근 몇 주로 좁히는 기준이고, "
            "분석한 주(market_causal_path.week_start)보다 미래일 수 없다"
        ),
    )
    first_seen_week: Mapped[date] = mapped_column(
        nullable=False,
        comment="이 사건을 처음 만든 분석 주의 월요일(KST). 어휘가 언제 자랐는지를 본다",
    )


class MarketChannel(EntityBase):
    """사건이 대상에 닿은 전달 경로 하나. 그래프의 가운데 노드다.

    **자연키가 이름 하나다.** 채널에는 날짜가 없다 — `할인율`은 언제 나와도 같은 `할인율`이고,
    그렇기 때문에 서로 다른 주의 경로들이 이 노드를 공유하며 사슬로 이어진다. 그것이 이
    설계에서 다중 홉이 생기는 유일한 자리다.

    이름에 **방향과 지역·종목을 넣지 않는다**(`위험회피 완화` ✗, `위험선호` ○). 방향은
    `market_causal_path.sign`이 담고 지역·종목은 사건과 대상이 말한다. 규칙을 안 지키면
    같은 경로가 여러 이름으로 갈라져 그래프가 이어지지 않는다 — 프롬프트가 그것을 막는다.
    """

    __tablename__ = "market_channel"
    __table_args__ = (
        UniqueConstraint("name", name="uq_market_channel_natural_key"),
        table_options(
            comment="주간 인과 그래프의 전달 경로 노드. 이름 하나가 자연키다",
            database="default",
        ),
    )

    name: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="경로 이름. 예: 할인율, 위험선호, 수급. 방향·지역·종목을 넣지 않는다",
    )
    first_seen_week: Mapped[date] = mapped_column(
        nullable=False,
        comment="이 경로를 처음 만든 분석 주의 월요일(KST). 어휘 수렴을 관측하는 값이다",
    )


class MarketCausalPath(EntityBase):
    """사건 하나가 대상 하나에 닿은 경로 하나의 헤더. 단계는 `market_causal_step`이 갖는다.

    **한 행이 경로 하나이고 그래프로 펴면 엣지 N+1개다.** 체인을 독립 엣지로만 저장하면
    `할인율 → 밸류에이션` 행이 어느 사건에서 나왔는지를 잃어 그 엣지가 모든 사건에 공유되고
    인과가 뭉개진다.

    **자연키에 `chain_key`가 들어간다.** 같은 사건이 같은 대상에 서로 다른 경로로 닿는 일이
    실제로 있기 때문이다 — 금리 인상이 `할인율`로는 은행주를 누르고 `예대마진`으로는 올린다.
    자연키가 그것을 못 담으면 두 번째 경로가 조용히 삼켜지고, 조용한 손실은 나중에 자연키를
    늘려도 되돌릴 수 없다.

    **출발점은 사건 또는 대상이다**(설계 §11.4). "US10Y가 내려서 SOX가 올랐다"를 담으려면
    앞 경로의 끝이 다음 경로의 시작이어야 하는데, 그것을 새 `MarketEvent`로 만들면
    `target:US10Y`와 `event:미국 국채금리 하락`이 **다른 노드**라 조회가 거기서 끊긴다.
    문자열만 깊어지고 홉은 늘지 않는다. 그래서 `event_id`를 nullable로 열고
    `source_target_*` 셋을 두되 CHECK로 **정확히 하나만** 채워지게 막는다.
    """

    __tablename__ = "market_causal_path"
    __table_args__ = (
        # **`event_id`가 아니라 `source_key`로 건다.** nullable이 된 `event_id`를 자연키에
        # 두면 PostgreSQL이 NULL을 서로 다른 값으로 봐서 대상 출발 경로가 중복 삽입된다.
        UniqueConstraint(
            "week_start",
            "source_key",
            "target_kind",
            "target_code",
            "chain_key",
            name="uq_market_causal_path_natural_key",
        ),
        CheckConstraint(
            "sign IN ('up', 'down')",
            name="ck_market_causal_path_sign",
        ),
        CheckConstraint(
            "confidence IN ('observed', 'endpoint_observed', 'plausible')",
            name="ck_market_causal_path_confidence",
        ),
        CheckConstraint(
            "target_kind IN ('instrument', 'index', 'quote', 'indicator')",
            name="ck_market_causal_path_target_kind",
        ),
        # 출발점은 둘 중 정확히 하나다. 둘 다이거나 둘 다 아닌 행을 DB가 막는다.
        CheckConstraint(
            "(event_id IS NOT NULL AND source_target_kind IS NULL"
            " AND source_target_code IS NULL AND source_sign IS NULL)"
            " OR (event_id IS NULL AND source_target_kind IS NOT NULL"
            " AND source_target_code IS NOT NULL AND source_sign IS NOT NULL)",
            name="ck_market_causal_path_source_exclusive",
        ),
        CheckConstraint(
            "source_sign IS NULL OR source_sign IN ('up', 'down')",
            name="ck_market_causal_path_source_sign",
        ),
        CheckConstraint(
            "source_target_kind IS NULL OR source_target_kind IN"
            " ('instrument', 'index', 'quote', 'indicator')",
            name="ck_market_causal_path_source_target_kind",
        ),
        # 대상에서 출발한 경로는 자기 자신으로 돌아올 수 없다.
        CheckConstraint(
            "source_target_code IS NULL"
            " OR NOT (source_target_kind = target_kind AND source_target_code = target_code)",
            name="ck_market_causal_path_source_not_self",
        ),
        # `endpoint_observed`는 값에서 나온다. 사건에서 출발한 경로는 쓸 수 없다.
        CheckConstraint(
            "confidence <> 'endpoint_observed' OR source_target_code IS NOT NULL",
            name="ck_market_causal_path_endpoint_needs_source",
        ),
        CheckConstraint(
            "return_unit IN ('percent', 'basis_point')",
            name="ck_market_causal_path_return_unit",
        ),
        table_options(
            comment="주간 인과 그래프의 경로 하나. 사건에서 대상까지의 주장과 실현 등락이다",
            database="default",
        ),
    )

    week_start: Mapped[date] = mapped_column(
        nullable=False,
        comment=(
            "분석한 주의 월요일(KST). 사건이 일어난 주이며 실현 등락 셋의 기준이기도 하다. "
            "사건 자체는 이 주보다 앞설 수 있다"
        ),
    )
    event_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("market_event.id", ondelete="RESTRICT"),
        nullable=True,
        comment=(
            "이 경로의 출발 사건. 지우면 그래프가 끊기므로 RESTRICT다. "
            "대상에서 출발한 경로는 NULL이고 그때 source_target_* 셋이 채워진다"
        ),
    )
    source_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "출발점을 한 칸에 담은 문자열. 사건이면 'e:<event_id>', "
            "대상이면 't:<kind>:<code>:<sign>'이다. chain_key와 같은 이유로 둔다 — "
            "nullable 컬럼을 자연키에 두면 NULL이 서로 달라 중복이 들어온다"
        ),
    )
    source_target_kind: Mapped[CausalTargetKind | None] = mapped_column(
        _enum_column(CausalTargetKind),
        nullable=True,
        comment="대상에서 출발한 경로의 원인 대상 종류. 사건 출발이면 NULL",
    )
    source_target_code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "대상에서 출발한 경로의 원인 대상 식별자. 같은 주 다른 경로의 대상이어야 한다. "
            "사건 출발이면 NULL"
        ),
    )
    source_sign: Mapped[CausalSign | None] = mapped_column(
        _enum_column(CausalSign),
        nullable=True,
        comment=(
            "원인 대상이 그 주에 움직인 방향(up 또는 down). 실현 등락의 부호와 맞아야 하고 "
            "저장 전에 코드가 대조한다. 사건 출발이면 NULL"
        ),
    )
    target_kind: Mapped[CausalTargetKind] = mapped_column(
        _enum_column(CausalTargetKind),
        nullable=False,
        comment="대상이 어느 마스터에서 오는지(instrument·index·quote·indicator)",
    )
    target_code: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="대상 식별자. 예: 005930, KOSPI, USDKRW, KTB10Y. 마스터 밖 값은 저장 전에 버린다",
    )
    chain_key: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "단계의 channel_id를 position 순서대로 '>'로 이은 문자열. "
            "market_causal_step의 비정규화이고 자연키를 헤더 한 행에 담기 위해 둔다"
        ),
    )
    sign: Mapped[CausalSign] = mapped_column(
        _enum_column(CausalSign),
        nullable=False,
        comment="모델이 주장한 방향(up 또는 down). 실현 등락과 엇갈려도 고치지 않는다",
    )
    confidence: Mapped[CausalConfidence] = mapped_column(
        _enum_column(CausalConfidence),
        nullable=False,
        comment=(
            "observed는 근거 문서가 방향을 직접 말함, endpoint_observed는 양 끝 값이 그렇게 "
            "움직임, plausible은 해석. 셋 다 인과의 증명이 아니다"
        ),
    )
    reasoning: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="이 경로를 설명하는 한 문장. 모델이 만든다",
    )
    return_week_change: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        comment=(
            "그 주 대상 변화(단위는 return_unit). SQL이 계산한다. "
            "경로가 작용했다고 주장하는 창이다"
        ),
    )
    return_t1_change: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        comment="주 종료 다음 KRX 거래일까지의 변화(단위는 return_unit). SQL이 계산한다",
    )
    return_t5_change: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        comment="주 종료 +5 KRX 거래일까지의 변화(단위는 return_unit). SQL이 계산한다",
    )
    return_unit: Mapped[CausalReturnUnit] = mapped_column(
        _enum_column(CausalReturnUnit),
        nullable=False,
        comment=(
            "실현 등락 셋의 단위. 가격·지수·환율은 percent, 금리는 basis_point다. "
            "조회하는 쪽은 이 칸을 반드시 걸어야 한다 — 안 걸면 7bp와 10%가 한 축에 섞인다"
        ),
    )
    input_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "이 경로를 만든 실행의 입력 해시(주·대상·후보 ref·프롬프트 판). "
            "재실행 판정이 아니라 감사 값이라 자연키에 넣지 않는다"
        ),
    )
    llm_run_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("thesis_llm_run.id", ondelete="SET NULL"),
        nullable=True,
        comment="이 경로를 만든 LLM 대화. 원장이 지워져도 경로는 남는다",
    )


class MarketCausalStep(EntityBase):
    """한 경로가 거친 전달 단계 하나. `position`이 1부터 사건 쪽에서 대상 쪽으로 는다.

    같은 채널이 여러 경로에 나타나고, 그 겹침이 그래프를 사슬로 잇는다.
    """

    __tablename__ = "market_causal_step"
    __table_args__ = (
        UniqueConstraint("path_id", "position", name="uq_market_causal_step_natural_key"),
        CheckConstraint(
            f"position BETWEEN 1 AND {MAX_CHAIN}",
            name="ck_market_causal_step_position",
        ),
        table_options(
            comment="주간 인과 그래프 경로의 전달 단계. 순서가 있는 자식 행이다",
            database="default",
        ),
    )

    path_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("market_causal_path.id", ondelete="CASCADE"),
        nullable=False,
        comment="이 단계가 속한 경로. 헤더가 지워지면 함께 지운다",
    )
    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment=f"단계 순서. 1이 사건에 가장 가깝고 최대 {MAX_CHAIN}이다. 빈 곳 없이 채운다",
    )
    channel_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("market_channel.id", ondelete="RESTRICT"),
        nullable=False,
        comment="이 단계의 전달 경로. 지우면 그래프가 끊기므로 RESTRICT다",
    )


class MarketCausalEvidence(EntityBase):
    """한 경로가 근거로 든 후보 하나.

    **`ref`는 `<kind>:<id>` 문자열이고 외래키를 걸지 않는다.** 근거가 `document`·
    `disclosure_event`·`technical_signal` 셋에 흩어져 있어 걸 대상이 하나가 아니고,
    걸면 마스터에 없는 근거 하나가 경로 전체를 죽인다 — `document_instrument`가 종목
    마스터를 참조하지 않는 것과 같은 판단이다. 목록 밖 값은 저장 전에 버려진다
    (`causal.generation.verify_paths`).

    이 테이블이 없던 동안 `confidence` 판정이 옳은지 볼 방법이 없었다(2026-08-28에 더했다).
    """

    __tablename__ = "market_causal_evidence"
    __table_args__ = (
        UniqueConstraint("path_id", "ref", name="uq_market_causal_evidence_natural_key"),
        table_options(
            comment="주간 인과 그래프 경로가 인용한 근거. 판정을 되짚는 자리다",
            database="default",
        ),
    )

    path_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("market_causal_path.id", ondelete="CASCADE"),
        nullable=False,
        comment="이 근거가 붙은 경로. 헤더가 지워지면 함께 지운다",
    )
    ref: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="후보 식별자. `document:84026`처럼 `<kind>:<id>` 규약이다",
    )
