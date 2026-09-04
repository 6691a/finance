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
        "filing_entity_id",
        "sector",
    }
    assert columns.source_symbol.nullable is True
    assert columns.is_watched.nullable is False
    assert columns.currency.nullable is False
    # 번호가 없다는 것은 규제 공시 대상이 아니라는 뜻이다. 빈 문자열로 메우면 그 뜻이 사라진다.
    assert columns.filing_entity_id.nullable is True
    assert columns.sector.nullable is True


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

    assert IndicatorSeries.__table__.comment == "지표 시계열이 어느 나라 무슨 값인지 설명하는 마스터"
    assert {column.name: column.comment for column in IndicatorSeries.__table__.columns} == {
        "id": "레코드 고유 식별자",
        "created_at": "레코드 생성 시각(UTC)",
        "updated_at": "레코드 최종 수정 시각(UTC)",
        "provider": "데이터 제공처 식별자(예: fred 또는 ecos). indicator_observation.provider와 같은 값이다",
        "series_id": "제공처 안에서 시계열을 가리키는 식별자. indicator_observation.series_id와 같은 값이다",
        "country": "발행 국가(ISO 3166-1 alpha-2, 예: US 또는 KR). 유로존처럼 국가가 아닌 통화권은 XM을 쓴다",
        "country_name": "국가 표시 이름. 국가에 붙는 속성이 더 늘면 country 마스터 테이블로 분리한다",
        "maturity_months": (
            "만기 개월 수. 만기별 비교와 정렬에 쓴다(3개월=3, 10년=120). 91일물은 3으로 둔다. "
            "물가지수처럼 만기 개념이 없는 지표는 NULL이다"
        ),
        "kind": (
            "시계열의 종류(government_bond, money_market, policy_rate, tips_rate, credit_spread, "
            "price_index, activity, balance_sheet, balance_sheet_item 또는 sentiment). 국채 곡선에서 "
            "단기 자금시장 금리·정책금리·실질금리·신용스프레드를 가르고, 단위가 다른 거시지표와 "
            "대차대조표 잔액과 설문이 만드는 심리지수를 그 곡선에서 뺀다"
        ),
        "label": "차트와 표에 쓰는 표시 이름(예: 미국 10년물)",
    }


def test_instrument_documents_table_and_column_purposes():
    from apps.models.reference import Instrument

    assert Instrument.__table__.comment == "우리가 이름을 아는 종목의 마스터. is_watched가 참인 종목만 시세를 받는다"
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
        "is_watched": "시세를 수집할 대상 여부. 거짓이면 문서 태그 후보로만 쓴다",
        "filing_entity_id": (
            "그 나라 공시 규제기관이 이 회사에 붙인 고유번호. 값이 있으면 규제 공시·실적 수집 대상이고 "
            "NULL이면 아니다. 발급 기관은 market이 정한다(kospi·kosdaq=금융감독원 DART 회사 고유번호 8자리, "
            "nyse·nasdaq=SEC EDGAR CIK). 그래서 읽는 쪽은 market을 함께 건다"
        ),
        "sector": (
            "이 종목이 대표하는 산업(예: 반도체, 자동차, 화장품). 한국 거시 지표를 회사가 아니라 "
            "산업 단위로 집계하기 위한 축이며 대표 기업이 교체돼도 이름이 바뀌지 않는다. "
            "값이 바뀌는 것이 전제라 Enum과 CHECK를 두지 않는다"
        ),
    }
