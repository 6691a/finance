from modules.briefing.table import display_width, render_table


def test_korean_letters_take_two_columns():
    """고정폭 글꼴에서 한글은 두 칸이다. `len`으로 세면 표가 어긋난다."""
    assert display_width("코스피") == 6
    assert display_width("KOSPI") == 5
    assert display_width("▲ +0.82%") == 8


def test_first_column_is_left_aligned_and_the_rest_right():
    table = render_table(
        ("구분", "종가", "등락"),
        (
            ("KOSPI", "2,687.45", "▲ +0.82%"),
            ("코스닥", "745.10", "▼ -0.31%"),
        ),
    )

    header, kospi, kosdaq = table.splitlines()
    # 첫 열은 이름이라 왼쪽에 붙는다.
    assert kospi.startswith("KOSPI ")
    assert kosdaq.startswith("코스닥 ")
    # 나머지 열은 오른쪽 정렬이라 자릿수가 다른 두 값의 끝이 맞는다.
    assert _end_column(kospi, "2,687.45") == _end_column(kosdaq, "745.10")
    assert header.endswith("등락")


def _end_column(line: str, value: str) -> int:
    """그 값이 끝나는 지점의 표시 칸 위치. 한글이 섞인 줄은 글자 수로 세면 어긋난다."""
    return display_width(line[: line.index(value) + len(value)])


def test_every_line_has_the_same_display_width():
    lines = render_table(
        ("구분", "값"),
        (("삼성전자", "1"), ("SK", "123456")),
    ).splitlines()

    assert len({display_width(line) for line in lines}) == 1


def test_empty_rows_render_headers_only():
    assert render_table(("구분", "값"), ()) == "구분  값"
