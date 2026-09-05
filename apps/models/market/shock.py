"""시장 급변 포착 — 30분 창에서 ±2% 움직인 사건 하나가 한 행.

설계는 `docs/analysis/market-shock-capture.md`다.

**일봉이 조용한 날에도 장중에는 일이 벌어진다.** 2026-09-03에 코스피가 30분 만에 −3.33%
빠졌다 되돌렸는데 종가는 +0.26%였다. 일봉에도, 그날 장중 뉴스에도 흔적이 없었다. 이 표는
그 사건이 있었다는 사실과, 며칠 뒤에야 오는 원인을 한자리에 둔다.

**포착과 원인이 한 표인 이유는 원인이 포착의 속성이기 때문이다.** 사건 없이 원인이 없고,
`cause_*` 칸이 채워지는 시점이 포착보다 하루에서 사흘 늦을 뿐이다. 표를 가르면 1:1 조인이
하나 늘고 "아직 원인이 없는 사건"을 세는 쿼리가 LEFT JOIN이 된다.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import EntityBase, table_options


def _enum_column(enum: type[StrEnum]) -> SqlEnum:
    """`StrEnum`을 VARCHAR + CHECK로 내리는 공통 형태.

    `apps/models/analysis/_columns.py`의 같은 함수와 글자 그대로 같다. 저쪽을 import하면
    `market`이 `analysis`에 의존하게 되는데, 두 도메인은 서로를 모르는 것이 맞다.
    """
    return SqlEnum(
        enum,
        native_enum=False,
        length=20,
        values_callable=lambda members: [member.value for member in members],
    )


class ShockDirection(StrEnum):
    """급변의 방향. **양방향이다** — 급등도 같은 얼굴의 사건이다.

    실측에서 ±2.0% 여섯 건이 하락 셋, 상승 셋으로 갈렸다(14거래일, 2026-09-04).
    """

    DROP = "drop"
    SURGE = "surge"


class ShockCauseStatus(StrEnum):
    """원인 분석의 상태.

    `pending`으로 열리고 `resolved` 또는 `unknown`으로 닫힌다. **닫히면 다시 안 본다** —
    안 닫으면 매일 같은 LLM 호출이 영원히 돈다.
    """

    PENDING = "pending"
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


class ShockCauseKind(StrEnum):
    """원인이 사실이었나 루머였나.

    2026-09-03의 일본은행 인상 관측처럼 **틀린 재료가 시장을 움직이고 되돌아오는** 일이
    있다. 그 구분이 없으면 나중에 읽는 사람이 "그때 정말 인상했나"를 다시 찾아야 한다.
    """

    RUMOR = "rumor"
    CONFIRMED = "confirmed"
    UNCLEAR = "unclear"


class MarketShockEvent(EntityBase):
    """30분 창에서 ±2% 움직인 사건 하나. 포착은 장중에, 원인은 사후에 채워진다.

    **자연키가 `(symbol, detected_at)`이다.** `detected_at`은 임계에 닿은 봉의 시각이라
    같은 창을 다시 보는 재실행이 같은 값을 낸다. 5분마다 도는 DAG가 한 급락을 여섯 번
    저장하지 않게 막는 것은 이 키가 아니라 **쿨다운**이다(`modules/shock/detect.py`) —
    낙폭이 깊어지면서 다른 봉이 임계에 닿으므로 키만으로는 안 막힌다.

    **`peers`가 비어 있는 시장을 0으로 적지 않는다.** 빈 칸은 "안 움직였다"가 아니라
    "못 봤다"이고, 둘을 섞으면 포착이 거짓말을 한다. 니케이가 15~16분 지연이라 실제로
    자주 일어난다.

    퍼센트 칸은 **0~1 정규화 규칙 밖이다**(물리량). `cause_kind`는 분류라 Enum이고
    점수가 아니다.
    """

    __tablename__ = "market_shock_event"
    __table_args__ = (
        UniqueConstraint(
            "symbol",
            "detected_at",
            name="uq_market_shock_event_natural_key",
        ),
        CheckConstraint(
            "direction IN ('drop', 'surge')",
            name="ck_market_shock_event_direction",
        ),
        CheckConstraint(
            "cause_status IN ('pending', 'resolved', 'unknown')",
            name="ck_market_shock_event_cause_status",
        ),
        CheckConstraint(
            "cause_kind IS NULL OR cause_kind IN ('rumor', 'confirmed', 'unclear')",
            name="ck_market_shock_event_cause_kind",
        ),
        CheckConstraint(
            "window_start < window_end",
            name="ck_market_shock_event_window_order",
        ),
        CheckConstraint(
            "cause_attempts >= 0",
            name="ck_market_shock_event_cause_attempts",
        ),
        # 원인 DAG가 매일 아침 이 조건으로 대상을 고른다. 행이 늘어도 pending은 한 줌이다.
        Index(
            "ix_market_shock_event_cause_pending",
            "cause_status",
            "cause_deadline",
        ),
        Index("ix_market_shock_event_session_date", "session_date"),
        table_options(
            comment="30분 창에서 ±2% 움직인 시장 급변과 그 사후 원인을 담는 테이블",
            database="default",
        ),
    )

    symbol: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="급변을 판정한 대상(KOSPI). index_bar.symbol과 같은 값이다. 대상이 늘 수 있어 Enum이 아니다",
    )
    session_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="사건이 일어난 세션 날짜(KST). 시각은 담지 않는다",
    )
    direction: Mapped[ShockDirection] = mapped_column(
        _enum_column(ShockDirection),
        nullable=False,
        comment="급변의 방향(drop은 고점 대비 하락, surge는 저점 대비 상승)",
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="임계에 닿은 봉의 시각(UTC). 자연키의 절반이다",
    )
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="판정한 창의 시작(UTC). 창 길이는 운영 손잡이라 행마다 남긴다",
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="판정한 창의 끝(UTC). 아시아 지수 지연을 흡수하려고 실행 시각보다 앞선다",
    )
    extreme_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="창 안의 극값 봉 시각(UTC). drop이면 고점, surge면 저점이다",
    )
    extreme_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False,
        comment="창 안의 극값. drop이면 최고가, surge면 최저가다. move_pct의 분모다",
    )
    trigger_price: Mapped[Decimal] = mapped_column(
        Numeric(18, 8),
        nullable=False,
        comment="임계에 닿은 봉의 값. drop이면 저가, surge면 고가다",
    )
    move_pct: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        comment="트리거 값(퍼센트). drop이면 음수, surge면 양수다. 부호가 direction과 짝이다",
    )
    window_change_pct: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4),
        nullable=True,
        comment=(
            "창의 첫 시가 대비 마지막 종가 등락(퍼센트). peers와 같은 눈금이라 나란히 읽는다. "
            "move_pct는 극값 기준이라 축이 다르다"
        ),
    )
    bar_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="판정에 쓴 봉 수. 창이 덜 찬 채로 판정했는지를 나중에 가른다",
    )
    peers: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        comment=(
            "같은 창의 다른 시장들. 대상마다 {symbol, change_pct, bars, available}이고 "
            "available이 false면 change_pct는 null이다. 0으로 채우지 않는다 — "
            "빈 칸은 '안 움직였다'가 아니라 '못 봤다'다"
        ),
    )
    threshold_pct: Mapped[Decimal] = mapped_column(
        Numeric(10, 4),
        nullable=False,
        comment="이 행을 만든 임계(퍼센트, 양수). 손잡이를 옮긴 뒤 옛 행과 섞이지 않게 남긴다",
    )
    notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="포착 Slack을 보낸 시각(UTC). NULL이면 저장은 됐고 발송이 실패한 것이다",
    )
    cause_status: Mapped[ShockCauseStatus] = mapped_column(
        _enum_column(ShockCauseStatus),
        nullable=False,
        server_default=ShockCauseStatus.PENDING.value,
        comment="원인 분석의 상태(pending은 아직, resolved는 찾음, unknown은 기한 안에 못 찾음)",
    )
    cause_deadline: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment=(
            "원인을 찾는 마지막 날(KST). 포착일부터 3번째 KRX 개장일이다. "
            "달력이 아직 그날까지 안 채워졌으면 NULL이고 다음 실행이 채운다"
        ),
    )
    cause_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="0",
        comment="원인 분석을 시도한 횟수. 모델을 부르기 전에 올린다 — 안 그러면 죽은 실행이 안 세어진다",
    )
    cause_text: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="원인 한 문장. resolved에서만 채워진다",
    )
    cause_kind: Mapped[ShockCauseKind | None] = mapped_column(
        _enum_column(ShockCauseKind),
        nullable=True,
        comment="원인이 루머로 밝혀졌나(rumor) 사실로 확인됐나(confirmed) 가릴 수 없나(unclear)",
    )
    cause_document_ids: Mapped[list | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="근거로 든 document.id 배열. 우리가 준 목록 안의 값만 남는다 — 검증이 나머지를 버린다",
    )
    cause_weak: Mapped[bool] = mapped_column(
        nullable=False,
        server_default="false",
        comment="검증이 근거를 전부 버렸다. 정상 답과 같아 보이면 아무도 그날을 못 고른다",
    )
    cause_search_used: Mapped[bool] = mapped_column(
        nullable=False,
        server_default="false",
        comment=(
            "그 답이 외부 검색 결과를 근거로 썼나. 우리 문서만으로 푼 건과 갈라야 "
            "'검색이 몇 %를 풀었나'를 셀 수 있고, 그 숫자가 소스를 늘릴지 검색을 끌지 정한다"
        ),
    )
    cause_prompt_version: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="원인 분석이 쓴 프롬프트 판. 판을 안 올리고 문장을 고치면 이 칸이 거짓말을 한다",
    )
    cause_llm_model: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="원인 분석이 부른 모델 이름",
    )
    cause_resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="원인 분석을 닫은 시각(UTC). resolved와 unknown 둘 다 채운다",
    )


class MarketShockSearchHit(EntityBase):
    """급변 하나의 원인을 찾다가 외부 검색으로 만난 기사 하나.

    **밖의 페이지는 바뀌고 사라진다.** 우리 `document`는 원본이 우리 DB에 있어서
    `market_shock_event.cause_document_ids`에 id만 남기면 되지만, 검색 결과는 우리가
    보관하지 않으면 근거가 증발한다. 그래서 이쪽만 표를 따로 둔다 — 비대칭이 아니라
    원본이 어디 있느냐의 차이다.

    **받은 것을 전부 남긴다.** 인용된 것만 남기면 "검색은 했는데 쓸 게 없었다"를 못 세는데,
    그것이 검색을 계속 쓸지 정하는 신호다. 질의 셋 × 10건 × 시도 셋이면 이벤트당 90행이
    상한이고 한 달 8.6건이면 연 9천 행 남짓이다.

    **본문을 긁지 않는다.** `snippet`이 이미 요지를 담는다(Tavily 실측 1,400자, 2026-09-04).
    남의 매체 본문을 통째로 받아 두는 것은 이용조건이 따로 있는 일이고, 어떤 매체가 반복해서
    답을 갖고 있으면 그때 `document_source`로 정식 편입한다.
    """

    __tablename__ = "market_shock_search_hit"
    __table_args__ = (
        UniqueConstraint(
            "shock_event_id",
            "url",
            name="uq_market_shock_search_hit_natural_key",
        ),
        CheckConstraint(
            "attempt >= 1 AND rank >= 1",
            name="ck_market_shock_search_hit_positions",
        ),
        CheckConstraint(
            "relevance IS NULL OR (relevance >= 0 AND relevance <= 1)",
            name="ck_market_shock_search_hit_relevance",
        ),
        Index("ix_market_shock_search_hit_event", "shock_event_id"),
        # 어느 매체가 반복해서 답을 갖는지 세는 조회. document_source 승격 판단의 근거다.
        Index("ix_market_shock_search_hit_publisher", "publisher", "cited"),
        table_options(
            comment="급변의 원인을 찾다가 외부 검색으로 만난 기사를 영구 보관하는 테이블",
            database="default",
        ),
    )

    shock_event_id: Mapped[int] = mapped_column(
        ForeignKey("market_shock_event.id", ondelete="CASCADE"),
        nullable=False,
        comment="이 결과를 찾게 만든 급변(market_shock_event.id)",
    )
    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="검색 제공처 식별자(tavily). 제공처를 갈아 끼울 때 옛 행과 섞이지 않게 남긴다",
    )
    query: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="이 결과를 처음 물어온 질의 전문. 질의 형태가 맞았는지를 뒤에서 이 칸으로 본다",
    )
    attempt: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="몇 번째 원인 분석 시도에서 처음 봤나(1부터)",
    )
    rank: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="그 질의 결과에서 몇 번째였나(1부터). 상위에서 답이 나오는지가 제공처 평가의 축이다",
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="기사 제목")
    url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="기사 원문 URL. 자연키의 절반이라 같은 기사가 여러 질의에서 나와도 한 행이다",
    )
    publisher: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="URL의 호스트(www.ebn.co.kr). 어느 매체를 document_source로 승격할지 이 값으로 센다",
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="제공처가 준 발행 시각(UTC). 안 주는 결과가 있어 nullable이고, 그때는 창 필터를 못 건다",
    )
    snippet: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="제공처가 준 발췌 전문. 본문을 따로 긁지 않으므로 이 칸이 우리가 본 것의 전부다",
    )
    relevance: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4),
        nullable=True,
        comment=(
            "제공처가 매긴 관련도(0~1). **우리가 만든 점수가 아니라 받은 값 그대로다** — "
            "제공처가 정한 눈금이라 정규화 규칙 밖이다"
        ),
    )
    cited: Mapped[bool] = mapped_column(
        nullable=False,
        server_default="false",
        comment="모델이 근거로 들었나. 검증이 끝난 뒤에 찍는다",
    )
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="우리가 이 스냅샷을 받은 시각(UTC). 밖의 페이지가 바뀌어도 그때 본 것이 이 행이다",
    )
