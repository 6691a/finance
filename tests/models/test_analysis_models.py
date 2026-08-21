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


def test_the_thesis_row_is_never_updated_after_it_is_written():
    from apps.models.analysis import Thesis

    names = {column.name for column in Thesis.__table__.columns}

    # 채점과 해설은 전부 thesis_outcome의 새 행이다. 여기 두면 두 번째 지평이 첫 판단을
    # 덮어써야 하고, 그것이 이 기능의 핵심 원칙과 정면으로 충돌한다.
    assert not names & {"evaluated_at", "actual_return_pct", "actual_outcome", "brier_score"}
    for column in ("prob_up", "prob_down", "prob_flat", "input_state"):
        assert Thesis.__table__.c[column].nullable is False


def test_thesis_outcome_separates_grading_from_narration():
    from apps.models.analysis import ThesisOutcome

    columns = ThesisOutcome.__table__.c

    # 채점은 pre_open만, 해설은 두 슬롯 모두라 양쪽 다 nullable이다.
    for name in ("evaluated_at", "actual_return_pct", "actual_outcome", "brier_score"):
        assert columns[name].nullable is True
    for name in ("narrative", "verdict", "narrative_at", "llm_model", "prompt_version"):
        assert columns[name].nullable is True
    # 어느 추론의 어느 지평인지는 항상 있다.
    for name in ("thesis_id", "horizon_days", "as_of_at", "dag_run_id"):
        assert columns[name].nullable is False


def test_analysis_enums_avoid_postgresql_native_types():
    from apps.models.analysis import Thesis, ThesisEvidence, ThesisOutcome

    enum_columns = (
        Thesis.__table__.c.run_slot,
        Thesis.__table__.c.subject_kind,
        ThesisOutcome.__table__.c.actual_outcome,
        ThesisOutcome.__table__.c.verdict,
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
        ThesisOutcome,
        ThesisSubjectKind,
        ThesisVerdict,
    )

    checks = {
        constraint.name: str(constraint.sqltext)
        for model in (Thesis, ThesisOutcome, ThesisEvidence)
        for constraint in model.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    # CHECK 문자열이 enum과 어긋나면 DB만 옛 값 집합을 강제한다. 값마다 대조한다.
    for name, enum in (
        ("ck_thesis_run_slot", RunSlot),
        ("ck_thesis_subject_kind", ThesisSubjectKind),
        ("ck_thesis_outcome_actual_outcome", ThesisDirection),
        ("ck_thesis_outcome_verdict", ThesisVerdict),
        ("ck_thesis_evidence_kind", ThesisEvidenceKind),
    ):
        for member in enum:
            assert f"'{member.value}'" in checks[name]


def test_the_horizon_lists_agree_between_the_model_and_its_checks():
    from apps.models.analysis import NARRATED_HORIZON_DAYS, THESIS_HORIZON_DAYS, ThesisEvidence, ThesisOutcome

    outcome_check = next(
        str(constraint.sqltext)
        for constraint in ThesisOutcome.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == "ck_thesis_outcome_horizon_days"
    )
    evidence_check = next(
        str(constraint.sqltext)
        for constraint in ThesisEvidence.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name == "ck_thesis_evidence_outcome_horizon_days"
    )

    # 지평 목록이 CHECK와 어긋나면 코드가 저장하려는 값을 DB가 거절한다.
    for horizon in THESIS_HORIZON_DAYS:
        assert str(horizon) in outcome_check
    for horizon in NARRATED_HORIZON_DAYS:
        assert str(horizon) in evidence_check
    # 지평 0은 해설을 받지 않아 근거도 없다.
    assert set(NARRATED_HORIZON_DAYS) == set(THESIS_HORIZON_DAYS) - {0}


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
