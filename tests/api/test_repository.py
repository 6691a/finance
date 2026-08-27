"""조회문 자체. **컴파일해서 본다** — 가짜 리포지토리는 SQL이 틀려도 통과한다."""

from datetime import date

from sqlalchemy.dialects import postgresql

from apps.api.repository import MAX_LIMIT, ThesisReadRepository


def compiled(statement) -> str:
    return str(statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))


def test_the_list_orders_by_time_not_by_slot_name():
    """`run_slot`은 문자열이라 정렬하면 시간이 뒤집힌다.

    `intraday_afternoon` → `intraday_midday` → `intraday_morning` → `post_close` →
    `pre_close` → `pre_open` 순이 된다. 슬롯의 진짜 시간 키는 `as_of_at`이다.
    """
    sql = compiled(ThesisReadRepository.list_statement(run_date_from=date(2026, 8, 1), run_date_to=date(2026, 8, 26)))

    order = sql[sql.index("ORDER BY") :]
    assert "thesis.as_of_at DESC" in order
    assert order.index("as_of_at") < order.index("subject_kind")


def test_the_list_always_carries_a_limit():
    """빠졌을 때의 사고 크기가 다르다. 날짜 구간이 넓으면 응답이 통째로 나간다."""
    sql = compiled(
        ThesisReadRepository.list_statement(
            run_date_from=date(2026, 8, 1),
            run_date_to=date(2026, 8, 26),
            limit=MAX_LIMIT,
        )
    )

    # `limit + 1`을 읽어 다음 쪽이 있는지 본다. 총 건수는 세지 않는다.
    assert f"LIMIT {MAX_LIMIT + 1}" in sql
    assert "count(" not in sql.lower()


def test_the_list_filters_are_optional_and_composable():
    both = compiled(
        ThesisReadRepository.list_statement(
            run_date_from=date(2026, 8, 1),
            run_date_to=date(2026, 8, 26),
            run_slots=["pre_open"],
            subject_codes=["KOSPI"],
        )
    )
    neither = compiled(
        ThesisReadRepository.list_statement(run_date_from=date(2026, 8, 1), run_date_to=date(2026, 8, 26))
    )

    assert "run_slot IN" in both and "subject_code IN" in both
    assert "run_slot IN" not in neither and "subject_code IN" not in neither


def test_the_grade_summary_folds_horizons_into_one_row_per_thesis():
    sql = compiled(ThesisReadRepository.outcome_summary_statement([1, 2]))

    assert "GROUP BY thesis_outcome.thesis_id" in sql
    # `count(evaluated_at)`은 NULL을 안 센다. 미채점 지평이 채점으로 보이면 안 된다.
    assert "count(thesis_outcome.evaluated_at)" in sql
    assert "count(thesis_outcome.narrative)" in sql
