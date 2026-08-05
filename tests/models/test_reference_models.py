from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy import Enum as SqlEnum


def test_instrument_inherits_common_identity_and_utc_timestamps():
    from apps.models.reference import Instrument

    columns = Instrument.__table__.c

    assert columns.id.primary_key
    assert columns.created_at.nullable is False
    assert columns.created_at.type.timezone is True
    assert columns.updated_at.nullable is False
    assert columns.updated_at.type.timezone is True


def test_instrument_keeps_its_natural_key_without_pinning_a_schema():
    from apps.models.reference import Instrument

    table = Instrument.__table__

    assert table.schema is None
    assert ("ticker", "market") in {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_instrument_keeps_optional_source_symbol_and_watch_flag():
    from apps.models.reference import Instrument

    columns = Instrument.__table__.c

    assert set(columns.keys()) == {
        "id",
        "created_at",
        "updated_at",
        "ticker",
        "market",
        "name",
        "kind",
        "currency",
        "source_symbol",
        "is_watched",
    }
    assert columns.source_symbol.nullable is True
    assert columns.is_watched.nullable is False
    assert columns.currency.nullable is False


def test_instrument_finite_domains_use_enums_with_check_constraints():
    from apps.models.reference import Instrument, InstrumentKind, Market

    columns = Instrument.__table__.c

    assert isinstance(columns.market.type, SqlEnum)
    assert columns.market.type.enum_class is Market
    assert isinstance(columns.kind.type, SqlEnum)
    assert columns.kind.type.enum_class is InstrumentKind

    check_names = {
        constraint.name for constraint in Instrument.__table__.constraints if isinstance(constraint, CheckConstraint)
    }
    assert {"ck_instrument_market", "ck_instrument_kind"} <= check_names


def test_instrument_documents_table_and_column_purposes():
    from apps.models.reference import Instrument

    assert Instrument.__table__.comment == "시세·뉴스·시그널이 참조하는 추적 종목 마스터"
    assert {column.name: column.comment for column in Instrument.__table__.columns} == {
        "id": "레코드 고유 식별자",
        "created_at": "레코드 생성 시각(UTC)",
        "updated_at": "레코드 최종 수정 시각(UTC)",
        "ticker": "거래 시장에서 사용하는 종목 코드",
        "market": "종목이 상장된 거래 시장(kospi, kosdaq, nyse 또는 nasdaq)",
        "name": "종목 표시 이름",
        "kind": "가격 수집 소스를 가르는 유형(equity, etf 또는 index)",
        "currency": "종목 가격의 표시 통화(ISO 4217, 예: KRW 또는 USD)",
        "source_symbol": "수집 소스에서 쓰는 심볼. 티커와 다를 때만 채운다(예: KOSPI → ^KS11)",
        "is_watched": "신규 데이터 수집과 분석을 수행할 추적 대상 여부",
    }
