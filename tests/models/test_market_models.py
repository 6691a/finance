from sqlalchemy import Enum as SqlEnum
from sqlalchemy import UniqueConstraint


def test_market_models_inherit_common_identity_and_utc_timestamps():
    from apps.models.market import IndicatorObservation
    from apps.models.raw import SourceRecord

    for model in (SourceRecord, IndicatorObservation):
        columns = model.__table__.c

        assert columns.id.primary_key
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

    assert ("series_id", "observation_date") in unique_columns
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
        == "FRED 지표 관측값을 조회 가능한 형태로 정규화하고 원본과 연결하는 테이블"
    )
    assert {column.name: column.comment for column in IndicatorObservation.__table__.columns} == {
        "id": "레코드 고유 식별자",
        "created_at": "레코드 생성 시각(UTC)",
        "updated_at": "레코드 최종 수정 시각(UTC)",
        "series_id": "공급자가 정의한 시계열 식별자(예: DGS10)",
        "observation_date": "지표 값의 기준일",
        "value": "정규화한 지표 값",
        "unit": "지표 값의 단위(예: Percent)",
        "source_record_id": "근거가 되는 source_record 레코드 ID",
    }
