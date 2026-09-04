"""조회문 자체. **컴파일해서 본다** — 가짜 리포지토리는 SQL이 틀려도 통과한다."""

from datetime import date

from sqlalchemy.dialects import postgresql

from apps.api.repository import MAX_LIMIT, ForecastReadRepository

FROM_DAY = date(2026, 8, 20)
TO_DAY = date(2026, 9, 3)


def compiled(statement) -> str:
    return str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )


def test_the_list_orders_by_time_not_by_slot_name():
    """`slot`은 문자열이라 정렬하면 하루가 뒤집힌다.

    `midday` → `pre_close` → `pre_open` 순이 되는데 실제 시각은 08:35 → 11:35 → 14:35이다.
    슬롯의 진짜 시간 키는 `as_of_at`이다.
    """
    sql = compiled(ForecastReadRepository.list_statement(run_from=FROM_DAY, run_to=TO_DAY))

    order = sql[sql.index("ORDER BY") :]
    assert "kospi_forecast.run_date DESC" in order
    assert "kospi_forecast.as_of_at ASC" in order
    assert "slot" not in order


def test_the_list_always_carries_a_limit():
    """빠졌을 때의 사고 크기가 다르다. 날짜 구간이 넓으면 응답이 통째로 나간다."""
    sql = compiled(
        ForecastReadRepository.list_statement(run_from=FROM_DAY, run_to=TO_DAY, limit=MAX_LIMIT)
    )

    # `limit + 1`을 읽어 다음 쪽이 있는지 본다. 총 건수는 세지 않는다.
    assert f"LIMIT {MAX_LIMIT + 1}" in sql
    assert "count(" not in sql.lower()


def test_the_list_leaves_the_heavy_jsonb_columns_behind():
    """`reasons`·`input_state`가 목록에 실리면 한 쪽이 메가 단위가 된다.

    문서 목록에서 같은 실수를 이미 했다 — `select *`가 55KB 응답에 6.8MB를 옮겼다.
    """
    sql = compiled(ForecastReadRepository.list_statement(run_from=FROM_DAY, run_to=TO_DAY))

    selected = sql[: sql.index("FROM")]
    assert "kospi_forecast.reasons" not in selected
    assert "kospi_forecast.input_state" not in selected
    # 목록이 쓰는 칸은 그대로 있다.
    assert "kospi_forecast.expected_change_pct" in selected
    assert "kospi_forecast.hit" in selected


def test_the_list_filters_are_optional_and_composable():
    both = compiled(
        ForecastReadRepository.list_statement(
            run_from=FROM_DAY, run_to=TO_DAY, slots=["pre_open"], graded=True
        )
    )
    neither = compiled(ForecastReadRepository.list_statement(run_from=FROM_DAY, run_to=TO_DAY))

    assert "slot IN" in both and "graded_at IS NOT NULL" in both
    assert "slot IN" not in neither and "graded_at IS NOT NULL" not in neither


def test_the_graded_filter_reads_one_column_because_the_four_move_together():
    """채점 칸 넷은 함께 있거나 함께 없다(모델 CHECK). 그래서 `graded_at` 하나로 가른다."""
    pending = compiled(
        ForecastReadRepository.list_statement(run_from=FROM_DAY, run_to=TO_DAY, graded=False)
    )

    assert "graded_at IS NULL" in pending
    assert "hit IS NULL" not in pending


def test_the_accuracy_summary_gives_sums_not_averages():
    """비율을 SQL에서 내면 슬롯 합계가 평균의 평균이 된다. 나눗셈은 서비스가 한 번만 한다."""
    sql = compiled(ForecastReadRepository.accuracy_statement(run_from=FROM_DAY, run_to=TO_DAY))

    assert "GROUP BY kospi_forecast.slot" in sql
    assert "avg(" not in sql.lower()
    assert "sum(abs(" in sql.lower()
    # 채점 전 행도 센다 — 빼면 표가 "아직 없다"를 말하지 못한다.
    assert "graded_at IS NULL" in sql
