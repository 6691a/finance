from datetime import date

from sqlalchemy import BigInteger, UniqueConstraint
from sqlalchemy import Enum as SqlEnum


def test_market_models_inherit_common_identity_and_utc_timestamps():
    from apps.models.market import IndicatorObservation
    from apps.models.raw import SourceRecord

    for model in (SourceRecord, IndicatorObservation):
        columns = model.__table__.c

        assert columns.id.primary_key
        # DB가 채우는 BIGSERIAL. 애플리케이션이 만든 값을 넣지 않는다.
        assert isinstance(columns.id.type, BigInteger)
        assert columns.id.autoincrement is True
        assert columns.id.default is None
        assert columns.id.server_default is None
        assert columns.created_at.nullable is False
        assert columns.created_at.type.timezone is True
        assert columns.updated_at.nullable is False
        assert columns.updated_at.type.timezone is True


def test_indicator_observation_keeps_natural_key_and_source_reference():
    from apps.models.market import IndicatorObservation

    table = IndicatorObservation.__table__
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    # series_id는 제공처 안에서만 고유하므로 provider가 자연키에 함께 들어간다.
    assert ("provider", "series_id", "observation_date") in unique_columns
    assert {foreign_key.target_fullname for foreign_key in table.c.source_record_id.foreign_keys} == {
        "source_record.id"
    }


def test_indicator_observation_indexes_source_reference():
    from apps.models.market import IndicatorObservation

    assert ("source_record_id",) in {
        tuple(column.name for column in index.columns) for index in IndicatorObservation.__table__.indexes
    }


def test_source_record_supports_generic_collectors_with_optional_raw_content():
    from apps.models.raw import SourceRecord

    columns = SourceRecord.__table__.c

    assert set(columns.keys()) == {
        "id",
        "created_at",
        "updated_at",
        "source_type",
        "source",
        "source_key",
        "started_at",
        "completed_at",
        "status",
        "record_count",
        "payload",
        "payload_uri",
        "metadata",
    }
    assert columns.started_at.type.timezone is True
    assert columns.completed_at.type.timezone is True
    assert columns.completed_at.nullable is True
    assert columns.payload.nullable is True
    assert columns.payload_uri.nullable is True
    assert columns.metadata.nullable is False


def test_finite_source_domains_use_enums():
    from apps.models.raw import SourceRecord, SourceStatus, SourceType

    assert isinstance(SourceRecord.__table__.c.source_type.type, SqlEnum)
    assert SourceRecord.__table__.c.source_type.type.enum_class is SourceType
    assert isinstance(SourceRecord.__table__.c.status.type, SqlEnum)
    assert SourceRecord.__table__.c.status.type.enum_class is SourceStatus


def test_market_models_document_table_and_column_purposes():
    from apps.models.market import IndicatorObservation
    from apps.models.raw import SourceRecord

    assert SourceRecord.__table__.comment == "API, 크롤링, 웹소켓 수집 단위의 출처와 상태를 보존하는 테이블"
    assert {column.name: column.comment for column in SourceRecord.__table__.columns} == {
        "id": "레코드 고유 식별자",
        "created_at": "레코드 생성 시각(UTC)",
        "updated_at": "레코드 최종 수정 시각(UTC)",
        "source_type": "수집 방식(api, crawl 또는 websocket)",
        "source": "데이터 제공처 식별자(예: fred 또는 kis)",
        "source_key": "공급자 내 원천 식별자(예: 시계열 ID, URL 또는 배치 ID)",
        "started_at": "수집 시작 시각(UTC)",
        "completed_at": "수집 완료 시각(UTC); 진행 중이면 NULL",
        "status": "수집 상태(예: running, succeeded, failed 또는 quarantined)",
        "record_count": "이 수집 단위에서 생성한 정규화 레코드 수",
        "payload": "작은 JSON 원본; 저장하지 않으면 NULL",
        "payload_uri": "대용량 원본의 외부 저장 위치; 없으면 NULL",
        "metadata": "HTTP 상태나 웹소켓 세션 ID 등 공급자별 부가 정보",
    }

    assert (
        IndicatorObservation.__table__.comment
        == "여러 제공처의 지표 관측값을 조회 가능한 형태로 정규화하고 원본과 연결하는 테이블"
    )
    assert {column.name: column.comment for column in IndicatorObservation.__table__.columns} == {
        "id": "레코드 고유 식별자",
        "created_at": "레코드 생성 시각(UTC)",
        "updated_at": "레코드 최종 수정 시각(UTC)",
        "provider": "데이터 제공처 식별자(예: fred 또는 ecos). 같은 수집의 source_record.source와 같은 값이다",
        "series_id": "제공처가 정의한 시계열 식별자(예: DGS10). 제공처 안에서만 고유하다",
        "observation_date": "지표 값의 기준일",
        "value": "정규화한 지표 값",
        "unit": "지표 값의 단위(예: Percent)",
        "source_record_id": "근거가 되는 source_record 레코드 ID",
    }


def test_market_session_keeps_its_natural_key_and_two_lineage_references():
    from apps.models.market import MarketSession

    table = MarketSession.__table__
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("market_code", "session_date") in unique_columns
    # 판정을 만든 수집과 그 행을 보강한 수집이 각각 남는다.
    assert table.c.source_record_id.nullable is False
    assert table.c.verification_source_record_id.nullable is True
    for column in (table.c.source_record_id, table.c.verification_source_record_id):
        foreign_key = next(iter(column.foreign_keys))
        assert foreign_key.target_fullname == "source_record.id"
        assert foreign_key.ondelete == "RESTRICT"


def test_market_session_closed_value_sets_are_enums_without_native_types():
    from apps.models.market import MarketCode, MarketSession, SessionVerifier

    columns = MarketSession.__table__.c

    for column, enum in ((columns.market_code, MarketCode), (columns.verified_by, SessionVerifier)):
        assert isinstance(column.type, SqlEnum)
        # PostgreSQL native enum은 값 추가·삭제 마이그레이션 비용이 커서 쓰지 않는다.
        assert column.type.native_enum is False
        assert column.type.length == 20
        assert set(column.type.enums) == {member.value for member in enum}

    assert MarketCode.US_EQUITY.value == "US_EQUITY"
    assert {member.value for member in SessionVerifier} == {"kis", "nyse"}


def test_market_session_verdict_is_nullable_so_unknown_days_do_not_block_collection():
    from apps.models.market import MarketSession

    columns = MarketSession.__table__.c

    # 모르는 날은 NULL이고 장중 수집기는 그 값을 개장과 같게 다룬다.
    assert columns.effective_open_day.nullable is True
    assert columns.verified_by.nullable is True
    # KIS 국내 원본은 미국 행에서 비어 있다.
    for name in ("kis_open_day", "kis_business_day", "kis_trading_day", "kis_settlement_day", "kis_weekday_code"):
        assert columns[name].nullable is True
    # 결제일은 국내 행에서 비어 있다.
    assert columns.local_settlement_date.nullable is True
    assert columns.domestic_settlement_date.nullable is True


def test_market_session_documents_every_column():
    from apps.models.market import MarketSession

    assert MarketSession.__table__.comment == "시장별·날짜별 개장 여부와 결제일을 저장하는 테이블"
    assert all(column.comment for column in MarketSession.__table__.columns)


def test_disclosure_event_keeps_the_receipt_date_and_the_detection_time_apart():
    from apps.models.market import DisclosureEvent

    columns = DisclosureEvent.__table__.c

    # 접수일은 날짜뿐이라 시각으로 꾸미지 않는다.
    assert columns.receipt_date.type.python_type is date
    # 최초 감지 시각은 항상 있고 UTC다.
    assert columns.detected_at.nullable is False
    assert columns.detected_at.type.timezone is True
    # 분 단위 접수 시각은 저장하지 않는다. 공식 RSS로는 과거를 채울 수 없다.
    assert "published_at" not in columns


def test_disclosure_event_keeps_its_natural_key_and_lineage_reference():
    from apps.models.market import DisclosureEvent

    table = DisclosureEvent.__table__
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("provider", "rcept_no") in unique_columns
    foreign_key = next(iter(table.c.source_record_id.foreign_keys))
    assert foreign_key.target_fullname == "source_record.id"
    assert foreign_key.ondelete == "RESTRICT"


def test_earnings_fact_is_not_tied_to_disclosure_event_by_a_foreign_key():
    from apps.models.market import EarningsFact

    table = EarningsFact.__table__

    # 실적 추출 실패가 공시 이벤트 수집을 막지 않도록 rcept_no로만 잇는다.
    assert table.c.rcept_no.foreign_keys == set()
    assert {foreign_key.target_fullname for foreign_key in table.c.source_record_id.foreign_keys} == {
        "source_record.id"
    }


def test_earnings_fact_closed_value_sets_are_enums_without_native_types():
    from apps.models.market import AmountBasis, EarningsFact, EarningsMetric, EarningsReleaseType, StatementScope

    columns = EarningsFact.__table__.c
    pairs = (
        (columns.release_type, EarningsReleaseType),
        (columns.statement_scope, StatementScope),
        (columns.amount_basis, AmountBasis),
        (columns.metric, EarningsMetric),
    )
    for column, enum in pairs:
        assert isinstance(column.type, SqlEnum)
        assert column.type.native_enum is False
        assert set(column.type.enums) == {member.value for member in enum}


def test_earnings_fact_allows_missing_comparisons_but_not_missing_amounts():
    from apps.models.market import EarningsFact

    columns = EarningsFact.__table__.c

    assert columns.current_amount.nullable is False
    # 전년 동기가 없는 공시가 있다. 0으로 바꾸지 않는다.
    assert columns.prior_year_amount.nullable is True
    # 잠정실적은 원문 표에서 읽으므로 계정 ID가 없다.
    assert columns.source_account_id.nullable is True


def test_dart_tables_document_every_column():
    from apps.models.market import DisclosureEvent, EarningsFact

    for model in (DisclosureEvent, EarningsFact):
        assert model.__table__.comment
        assert all(column.comment for column in model.__table__.columns)
