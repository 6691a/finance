from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy import Enum as SqlEnum

from tests.helpers import models_defined_in

# --- 등록 경로 (2026-08-25 패키지 분리) ---------------------------------------


def test_every_model_in_the_package_is_re_exported():
    """하위 모듈에 모델을 넣고 `analysis/__init__.py`에 이름을 안 더하면 그 테이블이
    `Base.metadata`에서 사라지고 autogenerate가 `DROP TABLE`을 낸다.

    `market` 패키지와 같은 보호다. 파일을 나눈 뒤 남는 유일한 조용한 사고 경로다.
    """
    import apps.models.analysis as package

    defined = models_defined_in(package)

    assert defined, "하위 모듈에서 모델을 하나도 못 찾았다. 훑는 방식이 깨졌다"
    missing = sorted(name for name in defined if name not in package.__all__)
    assert not missing, f"analysis/__init__.py의 __all__에 없다: {missing}"
    assert all(getattr(package, name, None) is model for name, model in defined.items())


def test_every_model_in_the_package_reaches_the_metadata():
    import apps.models.analysis as package
    from apps.core.database import Base

    for name, model in models_defined_in(package).items():
        assert model.__tablename__ in Base.metadata.tables, f"{name}이 metadata에 없다"












# --- 8단계(2026-08-24) 이벤트 기대치 -------------------------------------------


def test_event_models_are_exported_for_autogenerate():
    from apps import models

    # __all__에 없으면 Alembic autogenerate가 모델을 보지 못한다(프로젝트 규칙).
    for name in ("StockEventClaim", "StockEventExtraction", "StockEventOutcome"):
        assert name in models.__all__


def test_event_tables_follow_the_search_path():
    from apps.models.analysis import StockEventClaim, StockEventExtraction, StockEventOutcome

    for model in (StockEventClaim, StockEventExtraction, StockEventOutcome):
        assert model.__table__.schema is None
        assert model.__table__.info == {"database": "default", "managed": True}


def test_the_event_enums_match_their_check_constraints():
    from apps.models.analysis import (
        StockEventClaim,
        StockEventClaimKind,
        StockEventMetric,
        StockEventOutcome,
        StockEventType,
        SurpriseVerdict,
    )

    checks = {
        constraint.name: str(constraint.sqltext)
        for table in (StockEventClaim.__table__, StockEventOutcome.__table__)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    for member in StockEventType:
        assert f"'{member.value}'" in checks["ck_stock_event_claim_event_type"]
    for member in StockEventClaimKind:
        assert f"'{member.value}'" in checks["ck_stock_event_claim_kind"]
    for member in StockEventMetric:
        assert f"'{member.value}'" in checks["ck_stock_event_claim_metric"]
        assert f"'{member.value}'" in checks["ck_stock_event_outcome_metric"]
    for member in SurpriseVerdict:
        assert f"'{member.value}'" in checks["ck_stock_event_outcome_verdict"]


def test_the_event_enum_columns_are_not_native_postgres_enums():
    from apps.models.analysis import StockEventClaim, StockEventOutcome

    for table, column in (
        (StockEventClaim.__table__, "event_type"),
        (StockEventClaim.__table__, "claim_kind"),
        (StockEventOutcome.__table__, "verdict"),
    ):
        kind = table.c[column].type
        assert isinstance(kind, SqlEnum)
        # native enum은 값 추가·삭제 마이그레이션 비용이 커서 쓰지 않는다(프로젝트 규칙).
        assert kind.native_enum is False


def test_the_earnings_metrics_are_shared_with_the_earnings_fact_table():
    """판정이 `earnings_fact`를 대응표 없이 조인한다. 값이 갈리면 그 조인이 조용히 0행이 된다."""
    from apps.models.analysis import EVENT_METRICS, StockEventType
    from apps.models.market import EarningsMetric

    assert {metric.value for metric in EVENT_METRICS[StockEventType.EARNINGS]} == {
        member.value for member in EarningsMetric
    }


def test_a_judgment_is_written_once_and_never_updated():
    """첫 성공본 불변. 판정을 고치는 컬럼(상태·재판정 표시)을 두지 않는다."""
    from apps.models.analysis import StockEventOutcome

    names = {column.name for column in StockEventOutcome.__table__.columns}

    assert not names & {"status", "superseded_by", "revised_at", "revision"}
    for column in ("expected_value", "actual_value", "surprise_pct", "verdict", "announced_at", "actual_ref"):
        assert StockEventOutcome.__table__.c[column].nullable is False


def test_the_claim_source_columns_are_both_optional_but_guarded_by_a_check():
    from apps.models.analysis import StockEventClaim

    columns = StockEventClaim.__table__.c
    assert columns["document_id"].nullable is True
    assert columns["source_record_id"].nullable is True
    checks = {
        constraint.name
        for constraint in StockEventClaim.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "ck_stock_event_claim_source_xor" in checks


def test_the_extraction_ledger_keeps_one_row_per_document():
    from apps.models.analysis import StockEventExtraction

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in StockEventExtraction.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("document_id",) in unique_columns
    # 주장 0건도 남는다 — "뽑았는데 없었다"와 "아직 안 뽑았다"를 가른다.
    assert StockEventExtraction.__table__.c["claim_count"].nullable is False



def test_the_signal_direction_has_no_flat_and_no_thesis_dependency():
    """기술적 신호는 위·아래 둘뿐이다.

    전에는 옛 추론의 `ThesisDirection`을 빌려 썼고 거기엔 `FLAT`이 있었다. DB에는 들어간
    적이 없다 — `ck_technical_signal_direction`이 둘만 받았고 `_enum_column`은 CHECK를
    따로 만들지 않는다. 추론을 지우면서 이 표가 자기 enum을 갖는다.
    """
    from apps.models.analysis import SignalDirection

    assert {member.value for member in SignalDirection} == {"up", "down"}


def test_the_technical_model_does_not_import_the_thesis_model():
    """빌려 쓰던 의존을 끊은 것이 이 커밋의 전부다. 되살아나면 삭제가 다시 막힌다."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "apps/models/analysis/technical.py").read_text(
        encoding="utf-8"
    )

    assert "from apps.models.analysis.thesis" not in source


def test_the_disclosure_briefing_owns_its_dart_link():
    """공시 원문 링크는 추론과 무관하다. 쓰는 쪽이 갖는다."""
    from modules.briefing.disclosures import DART_VIEWER_URL

    assert DART_VIEWER_URL.format(rcept_no="20260903000123").endswith("rcpNo=20260903000123")
