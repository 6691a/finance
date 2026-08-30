from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy import Enum as SqlEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.core.database import EntityBase, table_options


class SourceKind(StrEnum):
    OFFICIAL = "official"
    MEDIA = "media"
    # 증권사 리서치. 공식기관도 언론도 아니고 `document_type`이 `report`로 갈리는 근거다.
    RESEARCH = "research"


class CollectionMode(StrEnum):
    """출처별로 어디까지 가져올지. 무료로 읽힌다는 것이 자동 수집 근거가 되지 않는다."""

    METADATA_ONLY = "metadata_only"
    FEED_CONTENT = "feed_content"
    FULL_TEXT = "full_text"


class BodyStatus(StrEnum):
    """본문을 받아 봤는가, 못 받았다면 왜인가.

    **컬럼이 NULL이면 "아직 해 보지 않았다"이고 그 집합이 곧 수집 큐다.** 그래서 여기에
    `failed`가 없다 — 연결 실패와 5xx는 상태를 남기지 않고 NULL로 두어 다음 실행이 다시
    집는다. 상태를 남기면 재시도 규칙을 따로 써야 한다.
    """

    OK = "ok"
    # 페이지에 글이 없다. 문단이 하나도 최소 길이를 넘지 않은 경우다.
    EMPTY = "empty"
    # 본문이 첨부에만 있다(한국은행·금감원·BOJ·네이버 리서치). 첨부는 받았다.
    ATTACHMENT_ONLY = "attachment_only"
    # 받을 수 없다. KRX는 문서별 GET 딥링크가 없고, HTTP 4xx는 다시 쳐도 같은 답이다.
    UNAVAILABLE = "unavailable"


class AttachmentKind(StrEnum):
    """첨부 하나가 내려받은 파일인지 링크만 남긴 영상인지."""

    FILE = "file"
    VIDEO = "video"


class DocumentType(StrEnum):
    ARTICLE = "article"
    REPORT = "report"
    PRESS_RELEASE = "press_release"
    SPEECH = "speech"


class Direction(StrEnum):
    """그 문서가 붙은 종목·지표에 호재인지 악재인지. 4단계 리포트가 쓴다."""

    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class DocumentSource(EntityBase):
    """문서를 어디서 어디까지 가져올지 정하는 출처 카탈로그.

    수집 정책이 코드가 아니라 데이터인 이유는 **이용조건이 출처마다 다르고 바뀌기 때문이다.**
    한 곳이 본문 자동수집을 막으면 `collection_mode`를 내리는 것으로 끝나야 하고, 그 판단
    시점을 `terms_checked_at`이 남긴다.

    `enabled=false`는 행은 두되 수집하지 않는 출처다. 지웠다가 다시 넣으면 왜 뺐는지가
    사라진다.
    """

    __tablename__ = "document_source"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_document_source_slug"),
        CheckConstraint("source_kind IN ('official', 'media', 'research')", name="ck_document_source_kind"),
        CheckConstraint(
            "collection_mode IN ('metadata_only', 'feed_content', 'full_text')",
            name="ck_document_source_collection_mode",
        ),
        table_options(
            comment="문서 수집 출처와 출처별 수집 정책을 보관하는 마스터",
            database="default",
        ),
    )

    slug: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="출처 식별자(예: fed, yonhap). 수집기 Enum과 같은 값이며 document.source_slug가 이 값을 쓴다",
    )
    name: Mapped[str] = mapped_column(Text, nullable=False, comment="출처 표시 이름")
    source_kind: Mapped[SourceKind] = mapped_column(
        SqlEnum(
            SourceKind,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="출처 종류(official, media, research). 공식기관 문서는 가치 점수와 무관하게 보관하고 research는 report로 저장한다",
    )
    country: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="출처 국가(ISO 3166-1 alpha-2). BIS처럼 국제기구는 NULL이다",
    )
    feed_url: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="발견 채널 URL(RSS 또는 Atom). 인증이 없어 그대로 저장한다",
    )
    collection_mode: Mapped[CollectionMode] = mapped_column(
        SqlEnum(
            CollectionMode,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment=(
            "어디까지 저장할지. metadata_only는 제목·URL만, feed_content는 피드가 준 요약까지, "
            "full_text는 원문 본문까지다. 이용조건에서 개인 자동수집이 확인된 출처만 full_text로 올린다"
        ),
    )
    language: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "이 출처가 쓰는 언어(ISO 639-1). 국가에서 추측하지 않고 출처마다 선언한다. "
            "한 출처가 여러 언어를 내보내면 그때 문서 단위 판별을 붙인다"
        ),
    )
    terms_url: Mapped[str | None] = mapped_column(Text, nullable=True, comment="이용조건 문서 URL")
    terms_checked_at: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="이용조건을 마지막으로 확인한 날짜(KST). collection_mode를 정한 근거 시점이다",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
        comment="수집 대상 여부. 끄더라도 행은 남겨 왜 뺐는지를 보존한다",
    )


class Document(EntityBase):
    """수집한 문서 한 건의 정규화 결과.

    소비자는 사람이 아니라 LLM이다. 그래서 **버리는 상태를 두지 않는다.** 승인·보류 같은
    상태 머신이 있으면 나중에 기준을 바꿀 때 이미 버린 문서를 되돌릴 수 없다. 전부 저장하고
    평가 점수만 남긴 뒤, 리포트를 만들 때 프롬프트가 상위 몇 개를 고른다.

    자연키는 `(source_slug, external_id)`다. **`content_hash`를 키에 넣지 않는다.** 넣으면
    본문이 조금만 달라져도 새 행이 생겨 같은 기사가 매시간 쌓인다. 본문이 바뀌면 같은 행을
    갱신하고, 다시 평가할지 말지는 `content_hash` 비교가 정한다.

    시각이 둘이고 뜻이 다르다. `published_at`은 제공처가 알려 준 발행 시각이고 없을 수 있다.
    `detected_at`은 우리가 그 문서를 처음 본 시각이라 항상 있다. `disclosure_event`가
    `receipt_date`와 `detected_at`을 나눠 두는 것과 같다.
    """

    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("source_slug", "external_id", name="uq_document_natural_key"),
        CheckConstraint(
            "document_type IN ('article', 'report', 'press_release', 'speech')",
            name="ck_document_type",
        ),
        CheckConstraint(
            "content_level IN ('metadata_only', 'feed_content', 'full_text')",
            name="ck_document_content_level",
        ),
        # 평가 전이면 NULL이다. SQL에서 NULL은 CHECK를 통과하므로 따로 열어 둘 필요가 없다.
        CheckConstraint(
            "direction IN ('positive', 'negative', 'neutral')",
            name="ck_document_direction",
        ),
        # 본문을 저장하지 않기로 한 출처의 문서에 본문이 들어가는 것을 DB가 막는다.
        # 수집기 판단이 어긋나도 여기서 걸린다.
        CheckConstraint(
            "content_level <> 'metadata_only' OR body IS NULL",
            name="ck_document_metadata_only_has_no_body",
        ),
        # 평가 전과 마찬가지로 본문 수집 전이면 NULL이다.
        CheckConstraint(
            "body_status IN ('ok', 'empty', 'attachment_only', 'unavailable')",
            name="ck_document_body_status",
        ),
        Index("ix_document_published_at", "published_at"),
        Index("ix_document_content_hash", "content_hash"),
        Index("ix_document_canonical_document_id", "canonical_document_id"),
        Index("ix_document_source_record_id", "source_record_id"),
        table_options(
            comment="수집한 경제 문서 한 건의 정규화 결과와 평가를 보관하는 테이블",
            database="default",
        ),
    )

    source_slug: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="document_source.slug와 같은 값. 외래키를 걸지 않아 마스터가 없어도 수집이 멈추지 않는다",
    )
    external_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="출처 안에서 문서를 가리키는 식별자. 제공처 ID가 없으면 정규화한 URL을 쓴다",
    )
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False, comment="문서 원문 URL")
    document_type: Mapped[DocumentType] = mapped_column(
        SqlEnum(
            DocumentType,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="문서 종류(article, report, press_release 또는 speech)",
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, comment="정규화한 제목")
    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="피드가 준 요약. 우리가 만든 요약이 아니다",
    )
    body: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="정규화한 본문. 길이 상한을 두지 않는다. metadata_only 출처는 NULL이며 CHECK 제약이 이를 강제한다",
    )
    body_status: Mapped[BodyStatus | None] = mapped_column(
        SqlEnum(
            BodyStatus,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=True,
        comment=(
            "본문 수집 결과(ok, empty, attachment_only 또는 unavailable). "
            "NULL은 아직 시도하지 않았다는 뜻이고 **그 집합이 곧 수집 큐다.** "
            "연결 실패와 5xx는 상태를 남기지 않아 다음 실행이 다시 집는다"
        ),
    )
    language: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="본문 언어(ISO 639-1, 예: ko, en). 검색 토크나이저를 가르는 값이다",
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="제공처가 알려 준 발행 시각(UTC). 피드가 주지 않으면 NULL이다",
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="이 문서를 처음 본 시각(UTC). 발행 시각과 달리 항상 있다",
    )
    content_level: Mapped[CollectionMode] = mapped_column(
        SqlEnum(
            CollectionMode,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment=(
            "이 문서에 실제로 담긴 수준. 출처 정책(document_source.collection_mode)과 다르다 — "
            "발견 시점에는 feed_content이고 본문이 들어온 뒤에야 full_text로 오른다"
        ),
    )
    content_hash: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "정규화한 제목·요약의 SHA-256. 재평가 여부와 완전 중복 판정의 기준이다. "
            "**본문은 넣지 않는다** — 평가가 제목과 요약만 보므로 본문이 바뀌었다고 다시 평가할 "
            "이유가 없고, 넣으면 본문 백필이 전체 문서의 재평가를 부른다. "
            "정규화 규칙이 흔들리면 이 값이 매번 바뀌므로 규칙을 먼저 고정한다"
        ),
    )
    canonical_document_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("document.id", ondelete="RESTRICT"),
        nullable=True,
        comment="중복일 때 대표 문서 ID. 대표 문서 자신은 NULL이다. 물리 삭제는 하지 않는다",
    )
    source_record_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("source_record.id", ondelete="RESTRICT"),
        nullable=False,
        comment="근거가 되는 source_record 레코드 ID",
    )
    direction: Mapped[Direction | None] = mapped_column(
        SqlEnum(
            Direction,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=True,
        comment="LLM이 본 방향(positive, negative 또는 neutral). 평가 전이면 NULL이다",
    )
    value_score: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment=(
            "관련성·새로움·구체성·영향의 0~2점 합계(0~8). **이 값으로 문서를 버리지 않는다.** "
            "리포트를 만들 때 프롬프트가 상위 몇 개를 고르는 데만 쓴다"
        ),
    )
    assessment: Mapped[dict[str, object] | None] = mapped_column(
        JSONB,
        nullable=True,
        comment="LLM 응답 전체(세부 점수, 주제, 새 사실, 판단 근거, 근거 청크). 조회 조건이 굳으면 컬럼으로 뺀다",
    )
    llm_model: Mapped[str | None] = mapped_column(Text, nullable=True, comment="평가에 쓴 모델 이름")
    prompt_version: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="평가에 쓴 프롬프트 버전. 이 값이 오르면 재평가 대상이 된다",
    )
    assessed_content_hash: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "평가 시점의 content_hash. 현재 content_hash와 다르면 본문이 바뀐 것이라 다시 평가한다. "
            "이 컬럼이 없으면 같은 문서를 매번 다시 평가하거나 영영 안 하거나 둘 중 하나가 된다"
        ),
    )
    assessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="평가를 마친 시각(UTC). 실패하면 NULL로 남아 다음 실행이 다시 집는다",
    )


class DocumentInstrument(EntityBase):
    """문서와 추적 종목을 잇는다.

    **이 테이블이 2단계의 핵심 산출물이다.** 리포트는 "지난 7일 005930 관련 기사"로 시작하는데
    자유 문자열 태그로는 그 조인이 안 된다. LLM에게는 후보 목록을 프롬프트로 주고, 목록 밖의
    값이 오면 그 태그만 버린다.

    **`instrument`로 외래키를 걸지 않는다.** `indicator_observation`이 `indicator_series`를
    참조하지 않는 것과 같은 이유다. 마스터에 없는 종목이 오면 태깅이 죽는 대신 그 태그만
    빠져야 한다. 마스터와의 대조는 테스트가 한다.
    """

    __tablename__ = "document_instrument"
    __table_args__ = (
        UniqueConstraint("document_id", "ticker", name="uq_document_instrument_natural_key"),
        Index("ix_document_instrument_ticker", "ticker"),
        table_options(
            comment="문서와 추적 종목을 잇는 태그 테이블",
            database="default",
        ),
    )

    document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("document.id", ondelete="CASCADE"),
        nullable=False,
        comment="문서 ID. 문서가 지워지면 태그도 함께 지운다",
    )
    ticker: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="instrument.ticker와 같은 값(예: 005930). 외래키를 걸지 않아 마스터가 없어도 태깅이 멈추지 않는다",
    )


class DocumentIndicator(EntityBase):
    """문서와 지표 시계열을 잇는다. `document_instrument`와 같은 규칙이다."""

    __tablename__ = "document_indicator"
    __table_args__ = (
        UniqueConstraint("document_id", "provider", "series_id", name="uq_document_indicator_natural_key"),
        Index("ix_document_indicator_series", "provider", "series_id"),
        table_options(
            comment="문서와 지표 시계열을 잇는 태그 테이블",
            database="default",
        ),
    )

    document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("document.id", ondelete="CASCADE"),
        nullable=False,
        comment="문서 ID. 문서가 지워지면 태그도 함께 지운다",
    )
    provider: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="지표 제공처(fred, ecos, yahoo 등). series_id는 제공처 안에서만 고유하다",
    )
    series_id: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="indicator_series.series_id 또는 quote_symbol.symbol과 같은 값(예: DGS10, USDKRW)",
    )


class DocumentAttachment(EntityBase):
    """문서에 붙은 파일과 영상 링크. 문서 하나에 여러 개일 수 있다.

    본문이 HTML에 없고 **첨부 PDF에만 있는 출처가 있다**(한국은행·금감원·BOJ·네이버 리서치).
    그 문서는 제목과 요약만으로는 검색되지 않으므로 파일을 받아 둔다. **텍스트로 바꾸는 일은
    아직 하지 않는다** — 파일을 먼저 모아 두면 변환 기능을 붙일 때 재수집이 필요 없다.

    영상은 내려받지 않고 링크만 남긴다(`kind='video'`, `storage_path`는 NULL). 기사가 글이
    아니라 영상인 경우가 있고, 그때 어디로 가면 볼 수 있는지가 남아야 한다.

    자연키는 `(document_id, url)`이다. **`position`을 키에 넣지 않는다** — 페이지 마크업이
    바뀌면 순서가 흔들려 같은 파일이 새 행이 된다.

    `document`로는 외래키를 **건다.** `document_instrument`가 `instrument`에 안 거는 것과
    다른 판단인데, 저기는 마스터가 늦게 채워지는 문제이고 첨부는 문서 없이 뜻이 없다.
    """

    __tablename__ = "document_attachment"
    __table_args__ = (
        UniqueConstraint("document_id", "url", name="uq_document_attachment_natural_key"),
        CheckConstraint("kind IN ('file', 'video')", name="ck_document_attachment_kind"),
        # 영상은 내려받지 않으므로 저장 경로가 없다. 파일은 받았으면 경로가 있어야 한다.
        CheckConstraint(
            "kind <> 'video' OR storage_path IS NULL",
            name="ck_document_attachment_video_has_no_path",
        ),
        Index("ix_document_attachment_document_id", "document_id"),
        table_options(
            comment="문서에 붙은 첨부 파일과 영상 링크",
            database="default",
        ),
    )

    document_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("document.id", ondelete="CASCADE"),
        nullable=False,
        comment="문서 ID. 문서가 지워지면 첨부도 함께 지운다",
    )
    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="문서 안에서의 순서(0부터). 페이지에 나온 차례일 뿐 자연키가 아니다",
    )
    kind: Mapped[AttachmentKind] = mapped_column(
        SqlEnum(
            AttachmentKind,
            native_enum=False,
            length=20,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        comment="내려받은 파일(file)인지 링크만 남긴 영상(video)인지",
    )
    url: Mapped[str] = mapped_column(Text, nullable=False, comment="첨부 원본 URL. 영상은 이 값이 전부다")
    storage_path: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "파일을 둔 자리의 상대경로(예: documents/boj/2026/08/1234-0.pdf). "
            "마운트 지점을 빼고 남기므로 마운트가 바뀌어도 행을 고치지 않는다. 영상은 NULL이다"
        ),
    )
    filename: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="제공처가 준 파일 이름. 알 수 없으면 NULL이다",
    )
    media_type: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="응답의 Content-Type(예: application/pdf). 제공처가 주지 않으면 NULL이다",
    )
    byte_size: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="받은 파일의 바이트 수. 크기 상한은 두지 않는다. 영상은 NULL이다",
    )
    sha256: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="받은 파일 내용의 SHA-256. 서로 다른 문서가 같은 파일을 가리키는지 보는 근거다. 영상은 NULL이다",
    )
    fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="파일을 내려받은 시각(UTC). 영상은 NULL이다",
    )
