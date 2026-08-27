from datetime import date

from sqlalchemy import BigInteger, UniqueConstraint
from sqlalchemy import Enum as SqlEnum

from tests.helpers import models_defined_in

# --- 등록 경로 (2026-08-25 패키지 분리) ---------------------------------------


def test_every_model_in_the_package_is_re_exported():
    """**등록은 클래스를 import하는 부수효과다.**

    `config.yaml`의 `model_modules`가 `apps.models`를 가리키고 `migrations/env.py`가 그것만
    import한다. 하위 모듈에 모델을 새로 넣고 `market/__init__.py`에 이름을 안 더하면
    `Base.metadata`에서 그 테이블이 사라지고, autogenerate가 "DB에는 있는데 모델에 없다"로
    읽어 `DROP TABLE`을 낸다. 파일을 나눈 뒤로 이것이 유일한 조용한 사고 경로다.
    """
    import apps.models.market as package

    defined = models_defined_in(package)

    assert defined, "하위 모듈에서 모델을 하나도 못 찾았다. 훑는 방식이 깨졌다"
    missing = sorted(name for name in defined if name not in package.__all__)
    assert not missing, f"market/__init__.py의 __all__에 없다: {missing}"
    assert all(getattr(package, name, None) is model for name, model in defined.items())


def test_every_model_in_the_package_reaches_the_metadata():
    """재수출까지 됐어도 metadata에 실제로 들어갔는지는 따로 본다."""
    import apps.models.market as package
    from apps.core.database import Base

    for name, model in models_defined_in(package).items():
        assert model.__tablename__ in Base.metadata.tables, f"{name}이 metadata에 없다"


def test_the_package_re_exports_nothing_that_moved_away():
    """`apps/models/__init__.py`가 여기서 가져가는 이름이 전부 살아 있는지 본다."""
    import apps.models.market as package

    for name in package.__all__:
        assert hasattr(package, name), f"__all__에 있는데 실제로 없다: {name}"


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


def test_market_movement_snapshot_keeps_its_natural_key_and_lineage():
    from apps.models.market import MarketMovementSnapshot

    table = MarketMovementSnapshot.__table__
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("provider", "symbol", "observed_at") in unique_columns
    foreign_key = next(iter(table.c.source_record_id.foreign_keys))
    assert foreign_key.target_fullname == "source_record.id"
    assert foreign_key.ondelete == "RESTRICT"


def test_market_movement_symbol_matches_the_quote_bar_vocabulary():
    from apps.models.market import KrxMarket
    from modules.collectors.market.kis_quote import MOVEMENT_INDEXES

    # 값이 quote_bar.symbol 과 글자 그대로 같아야 두 테이블을 한 키로 잇는다.
    assert {member.value for member in KrxMarket} == {index.value for index in MOVEMENT_INDEXES}


def test_market_movement_stores_counts_raw_without_ratios():
    from apps.models.market import MarketMovementSnapshot

    columns = {column.name for column in MarketMovementSnapshot.__table__.columns}

    assert {
        "upper_limit_count",
        "rising_count",
        "unchanged_count",
        "falling_count",
        "lower_limit_count",
    } <= columns
    # 상승이 상한가를 포함하는지 확인 전이라 비율·합계를 저장하지 않는다.
    assert not [name for name in columns if "ratio" in name or "total" in name]


def test_market_movement_documents_every_column():
    from apps.models.market import MarketMovementSnapshot

    assert MarketMovementSnapshot.__table__.comment
    assert all(column.comment for column in MarketMovementSnapshot.__table__.columns)


def test_positioning_tables_share_the_stock_code_vocabulary():
    from apps.models.market import (
        DisclosureEvent,
        KrxCreditBalanceRankingDaily,
        KrxStockCreditBalanceDaily,
        KrxStockSecuritiesLendingDaily,
        KrxStockShortSaleDaily,
    )

    # 공시와 포지션을 한 키로 이으려면 같은 이름의 같은 체계여야 한다.
    for model in (
        KrxStockCreditBalanceDaily,
        KrxStockShortSaleDaily,
        KrxStockSecuritiesLendingDaily,
        KrxCreditBalanceRankingDaily,
    ):
        assert "stock_code" in model.__table__.c
        assert "symbol" not in model.__table__.c
    assert "stock_code" in DisclosureEvent.__table__.c


def test_positioning_tables_document_every_column():
    from apps.models.market import (
        KrxCreditBalanceRankingDaily,
        KrxMarketFundsDaily,
        KrxStockCreditBalanceDaily,
        KrxStockSecuritiesLendingDaily,
        KrxStockShortSaleDaily,
    )

    for model in (
        KrxStockCreditBalanceDaily,
        KrxCreditBalanceRankingDaily,
        KrxMarketFundsDaily,
        KrxStockShortSaleDaily,
        KrxStockSecuritiesLendingDaily,
    ):
        assert model.__table__.comment
        assert all(column.comment for column in model.__table__.columns)


def test_investor_flow_tables_keep_the_shared_vocabularies():
    from apps.models.market import InvestorFlowMarketCode, MarketInvestorFlowSnapshot, StockInvestorEstimateSnapshot

    # 종목은 6자리 코드, 시장은 InvestorFlowMarketCode. 셋째 어휘를 만들지 않는다.
    assert "stock_code" in StockInvestorEstimateSnapshot.__table__.c
    assert "target" not in StockInvestorEstimateSnapshot.__table__.c
    market_column = MarketInvestorFlowSnapshot.__table__.c.market_code
    assert set(market_column.type.enums) == {member.value for member in InvestorFlowMarketCode}


def test_the_derivative_markets_do_not_leak_into_krx_market():
    """`KrxMarket`은 현물 두 시장으로 닫혀 있다.

    상승·보합·하락 분포와 시장 대차 잔고가 그 Enum을 쓴다. 거기에 콜옵션이 들어가면
    "시장 전체 종목 수"라는 뜻이 무너진다. 수급만 파생까지 넓히는 것이라 Enum을 나눴다.
    """
    from apps.models.market import InvestorFlowMarketCode, KrxMarket

    assert {member.value for member in KrxMarket} == {"KOSPI", "KOSDAQ"}
    assert {member.value for member in KrxMarket} < {member.value for member in InvestorFlowMarketCode}


def test_the_estimate_table_names_itself_an_estimate():
    from apps.models.market import StockInvestorEstimateSnapshot

    # 확정치가 아니라는 것이 이름과 주석에 드러나야 화면이 잘못 부르지 않는다.
    assert "estimate" in StockInvestorEstimateSnapshot.__tablename__
    assert "추정" in StockInvestorEstimateSnapshot.__table__.comment
    assert "source_time_code" in StockInvestorEstimateSnapshot.__table__.c


def test_investor_flow_tables_document_every_column():
    from apps.models.market import MarketInvestorFlowSnapshot, StockInvestorEstimateSnapshot

    for model in (StockInvestorEstimateSnapshot, MarketInvestorFlowSnapshot):
        assert model.__table__.comment
        assert all(column.comment for column in model.__table__.columns)


def test_stock_bar_tracks_ingest_method_and_finality():
    from sqlalchemy import CheckConstraint

    from apps.models.market import StockBar

    table = StockBar.__table__
    ingest_method = table.c.ingest_method
    is_final = table.c.is_final

    # WebSocket 잠정봉과 REST 확정봉을 가르는 축이다. default를 두지 않아
    # 모든 INSERT가 두 컬럼을 명시해야 한다.
    assert ingest_method.nullable is False
    assert ingest_method.server_default is None
    assert ingest_method.comment
    assert is_final.nullable is False
    assert is_final.server_default is None
    assert is_final.comment
    assert "ck_stock_bar_ingest_method" in {
        constraint.name for constraint in table.constraints if isinstance(constraint, CheckConstraint)
    }


def test_the_future_daily_table_keeps_the_real_contract():
    """논리 심볼(KOSPI200_FUT)과 실제 월물(A01609)을 함께 보존한다.

    분봉 `index_future_bar`가 이미 같은 칸을 갖고 있다. 이게 없으면 월물이 바뀐 날의 갭이
    시장 급변인지 롤오버인지 구분되지 않는다. Yahoo 연속 심볼(`ES=F`)은 실제 월물이 없어 NULL이다.
    """
    from apps.models.market import IndexDaily, IndexFutureDaily

    column = IndexFutureDaily.__table__.c.contract_code
    assert column.nullable is True
    assert column.comment

    assert "contract_code" not in IndexDaily.__table__.c
