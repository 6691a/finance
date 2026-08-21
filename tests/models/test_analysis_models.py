from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy import Enum as SqlEnum


def test_analysis_models_are_exported_for_autogenerate():
    from apps import models

    # __all__에 없으면 Alembic autogenerate가 모델을 보지 못한다(프로젝트 규칙).
    assert "Thesis" in models.__all__
    assert "ThesisEvidence" in models.__all__
    assert models.Thesis.__table__.name == "thesis"
    assert models.ThesisEvidence.__table__.name == "thesis_evidence"


def test_analysis_tables_follow_the_search_path():
    from apps.models.analysis import Thesis, ThesisEvidence

    # 파일 이름 analysis.py는 도메인 구분일 뿐이고 PostgreSQL 스키마가 아니다.
    for model in (Thesis, ThesisEvidence):
        assert model.__table__.schema is None
        assert model.__table__.info == {"database": "default", "managed": True}


def test_thesis_keeps_one_row_per_slot_and_subject():
    from apps.models.analysis import Thesis

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in Thesis.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }

    assert ("run_date", "run_slot", "subject_kind", "subject_code") in unique_columns


def test_thesis_grading_columns_stay_nullable_together():
    from apps.models.analysis import Thesis

    columns = Thesis.__table__.c

    # 채점은 넷이 한 번에 채워지거나 전부 비어 있다. 미채점 상태가 기본이다.
    for column in (columns.evaluated_at, columns.actual_return_pct, columns.actual_outcome, columns.brier_score):
        assert column.nullable is True
    for column in (columns.prob_up, columns.prob_down, columns.prob_flat, columns.input_state):
        assert column.nullable is False


def test_analysis_enums_avoid_postgresql_native_types():
    from apps.models.analysis import Thesis, ThesisEvidence

    enum_columns = (
        Thesis.__table__.c.run_slot,
        Thesis.__table__.c.subject_kind,
        Thesis.__table__.c.actual_outcome,
        ThesisEvidence.__table__.c.evidence_kind,
    )

    for column in enum_columns:
        assert isinstance(column.type, SqlEnum)
        # native enum은 값 추가·삭제 마이그레이션 비용이 커서 쓰지 않는다(프로젝트 규칙).
        assert column.type.native_enum is False
        assert column.type.length == 20


def test_analysis_check_constraints_repeat_the_enum_values():
    from apps.models.analysis import (
        RunSlot,
        Thesis,
        ThesisDirection,
        ThesisEvidence,
        ThesisEvidenceKind,
        ThesisSubjectKind,
    )

    checks = {
        constraint.name: str(constraint.sqltext)
        for model in (Thesis, ThesisEvidence)
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    # CHECK 문자열이 enum과 어긋나면 DB만 옛 값 집합을 강제한다. 값마다 대조한다.
    for name, enum in (
        ("ck_thesis_run_slot", RunSlot),
        ("ck_thesis_subject_kind", ThesisSubjectKind),
        ("ck_thesis_actual_outcome", ThesisDirection),
        ("ck_thesis_evidence_kind", ThesisEvidenceKind),
    ):
        for member in enum:
            assert f"'{member.value}'" in checks[name]


def test_thesis_evidence_follows_its_thesis_on_delete():
    from apps.models.analysis import ThesisEvidence

    foreign_key = next(iter(ThesisEvidence.__table__.c.thesis_id.foreign_keys))

    # 근거는 추론 없이 의미가 없다. 반대로 evidence_ref는 마스터로 외래키를 걸지 않는다.
    assert foreign_key.target_fullname == "thesis.id"
    assert foreign_key.ondelete == "CASCADE"
    assert not ThesisEvidence.__table__.c.evidence_ref.foreign_keys


def test_airflow_and_backend_agree_on_the_direction_vocabulary():
    from apps.models.analysis import ThesisDirection as BackendDirection
    from modules.thesis import ThesisDirection as AirflowDirection

    # Airflow는 apps/를 보지 못해 값을 한 벌 더 든다. 중복은 허용하되 여기서 대조한다.
    assert {member.value for member in AirflowDirection} == {member.value for member in BackendDirection}
