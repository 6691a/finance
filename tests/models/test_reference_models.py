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


def test_indicator_series_keeps_the_observation_join_key_as_its_natural_key():
    from apps.models.reference import IndicatorSeries

    table = IndicatorSeries.__table__

    assert table.schema is None
    # 대시보드가 indicator_observation 을 이 키로 조인한다.
    assert ("provider", "series_id") in {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def test_indicator_series_describes_country_maturity_and_kind():
    from apps.models.reference import IndicatorSeries, SeriesKind

    columns = IndicatorSeries.__table__.c

    assert set(columns.keys()) == {
        "id",
        "created_at",
        "updated_at",
        "provider",
        "series_id",
        "country",
        "country_name",
        "maturity_months",
        "kind",
        "label",
    }
    assert isinstance(columns.kind.type, SqlEnum)
    assert columns.kind.type.enum_class is SeriesKind

    check_names = {
        constraint.name
        for constraint in IndicatorSeries.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {"ck_indicator_series_kind", "ck_indicator_series_maturity_months"} <= check_names


def test_indicator_series_does_not_constrain_observations():
    from apps.models.reference import IndicatorSeries

    # 관측값에서 이 마스터로 외래키를 걸지 않는다. 걸면 마스터 행이 없는 시계열을 수집기가
    # 저장하지 못해 Enum에만 추가한 순간 DAG가 죽는다. 어긋남은 테스트가 잡는다.
    assert all(not column.foreign_keys for column in IndicatorSeries.__table__.columns)


def test_indicator_series_documents_table_and_column_purposes():
    from apps.models.reference import IndicatorSeries

    assert IndicatorSeries.__table__.comment == "지표 시계열이 어느 나라 무슨 금리인지 설명하는 마스터"
    assert {column.name: column.comment for column in IndicatorSeries.__table__.columns} == {
        "id": "레코드 고유 식별자",
        "created_at": "레코드 생성 시각(UTC)",
        "updated_at": "레코드 최종 수정 시각(UTC)",
        "provider": "데이터 제공처 식별자(예: fred 또는 ecos). indicator_observation.provider와 같은 값이다",
        "series_id": "제공처 안에서 시계열을 가리키는 식별자. indicator_observation.series_id와 같은 값이다",
        "country": "발행 국가(ISO 3166-1 alpha-2, 예: US 또는 KR). 유로존처럼 국가가 아닌 통화권은 XM을 쓴다",
        "country_name": "국가 표시 이름. 국가에 붙는 속성이 더 늘면 country 마스터 테이블로 분리한다",
        "maturity_months": "만기 개월 수. 만기별 비교와 정렬에 쓴다(3개월=3, 10년=120). 91일물은 3으로 둔다",
        "kind": "금리의 종류(government_bond 또는 money_market). 국채 곡선에서 단기 자금시장 금리를 가른다",
        "label": "차트와 표에 쓰는 표시 이름(예: 미국 10년물)",
    }


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
