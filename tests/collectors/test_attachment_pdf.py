import hashlib
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Self

import pymupdf
import pytest
from sqlalchemy import Table

from apps.models.content import DocumentAttachment as AttachmentModel
from modules.collectors.document.pdf import (
    MARGIN_BAND_RATIO,
    MAX_TABLE_CELL_CHARS,
    PARSE_UPDATE,
    PARSER_VERSION,
    PENDING_PARSES,
    SIDE_STRIP_MAX_WIDTH,
    AttachmentPdfParser,
    PagePiece,
    ParseCandidate,
    ParsedAttachment,
    ParsedPage,
    assemble,
    column_gutter,
    drop_running_frame,
    markdown_table,
    normalize_frame,
    pending_attachments,
    read_page,
    reading_order,
    running_frame,
    side_strip,
    usable_grid,
)

# 표 하나와 문단 하나가 있는 리포트 한 쪽을 흉내 낸다. 폰트는 한글이 들어가는 CJK 내장
# 폰트다 — `china-s`로 그리면 한글이 점으로 나온다(실측).
FONT = "korea"
TABLE = [["구분", "2025", "2026(E)"], ["영업이익", "1,200", "1,450"], ["매출액", "9,800", "10,500"]]


class FakeCursor:
    def __init__(self, rows: list | None = None) -> None:
        self.calls: list[tuple[str, object]] = []
        self.rows = rows or []
        self.rowcount = 1

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def execute(self, statement: str, parameters: object = ()) -> None:
        self.calls.append((statement, parameters))

    def fetchall(self) -> list:
        return self.rows


class FakeConnection:
    def __init__(self, rows: list | None = None) -> None:
        self.recorded_cursor = FakeCursor(rows)

    def cursor(self) -> FakeCursor:
        return self.recorded_cursor


def report_pdf(path: Path) -> str:
    """문단 + 표가 있는 첫 쪽과 문단만 있는 둘째 쪽. SHA-256을 돌려준다."""
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((60, 70), "한국은행 금융통화위원회는 기준금리를 동결했다.", fontname=FONT, fontsize=11)

    left, top, column, row = 60, 110, 120, 24
    for index in range(len(TABLE) + 1):
        page.draw_line(pymupdf.Point(left, top + index * row), pymupdf.Point(left + column * 3, top + index * row))
    for index in range(4):
        page.draw_line(
            pymupdf.Point(left + index * column, top), pymupdf.Point(left + index * column, top + row * len(TABLE))
        )
    for r, line in enumerate(TABLE):
        for c, cell in enumerate(line):
            page.insert_text((left + c * column + 5, top + r * row + 16), cell, fontname=FONT, fontsize=10)

    document.new_page().insert_text((60, 70), "둘째 쪽 본문이다.", fontname=FONT, fontsize=11)
    document.save(path)
    document.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def candidate(name: str = "probe.pdf") -> ParseCandidate:
    return ParseCandidate(id=7, storage_path=name)


def test_a_grid_needs_fill_rows_columns_and_a_number():
    """넷 중 하나라도 못 넘기면 표가 아니다. 레이아웃용 격자가 여기서 걸린다."""
    assert usable_grid(TABLE)
    # 열이 하나면 표가 아니라 목록이다.
    assert not usable_grid([["구분"], ["영업이익"]])
    # 행이 하나면 머리글 줄이다.
    assert not usable_grid([["구분", "2025", "2026"]])
    # 숫자가 없으면 우리가 찾는 표가 아니다.
    assert not usable_grid([["구분", "작년", "올해"], ["항목", "증가", "감소"]])
    # 채움률이 낮은 격자는 조판 틀이다.
    assert not usable_grid([["구분", None, None], [None, None, "1,200"]])


def test_a_grid_whose_cell_swallowed_a_whole_row_is_not_a_table():
    """열 경계를 덜 찾으면 한 셀에 행이 통째로 들어간다. 그 격자는 표가 아니다.

    앞의 셋(채움률·행·열·숫자)을 전부 통과하므로 이 문턱이 없으면 채택된다. 채택되면
    `read_page`가 **원본 블록을 버린 자리에** 뭉개진 표를 넣어 페이지 텍스트가 원본보다
    나빠진다. 실제 사례는 17열 ETF 표가 5열로 잡혀 헤더와 첫 행이 한 셀에 뭉친 것이다
    (2026-09-01 실측, 125자).
    """
    crammed = "TER 샤프 IR 테마 세부테마 티커 ETF명 지역 신재생 전기차 KARS " + "KraneShares Electric Vehicles " * 3
    assert len(crammed) > MAX_TABLE_CELL_CHARS
    assert not usable_grid([["ETF", "수익률"], [crammed, "0.6 4.9 2.8 (16.1)"]])

    # 문턱 바로 아래는 그대로 표다. 긴 셀이 있다고 다 버리지 않는다.
    assert usable_grid([["구분", "값"], ["가" * MAX_TABLE_CELL_CHARS, "1,200"]])


def test_the_markdown_table_keeps_one_shape():
    """표기가 갈리면 한 문서 안에 표가 두 모양으로 섞인다."""
    rendered = markdown_table([["구분", "값"], ["영업이익|주석", "1,200\n(잠정)"]])

    assert rendered.splitlines() == [
        "| 구분 | 값 |",
        "| --- | --- |",
        "| 영업이익/주석 | 1,200 (잠정) |",
    ]


def test_a_ragged_grid_is_padded_to_the_widest_row():
    rendered = markdown_table([["구분", "2025", "2026"], ["영업이익", "1,200"]])

    assert rendered.splitlines()[-1] == "| 영업이익 | 1,200 |  |"


def test_assemble_marks_every_page_and_returns_none_when_nothing_was_read():
    pages = [
        ParsedPage(number=1, text="첫 쪽", visible_chars=3, image_area_ratio=0.0),
        ParsedPage(number=2, text="", visible_chars=0, image_area_ratio=0.0),
        ParsedPage(number=3, text="셋째 쪽", visible_chars=4, image_area_ratio=0.0),
    ]

    assert assemble(pages) == "<!-- page:1 -->\n첫 쪽\n\n<!-- page:3 -->\n셋째 쪽"
    # 빈 문자열을 저장하면 "읽었는데 비었다"와 "안 읽었다"가 같아 보인다.
    assert assemble([pages[1]]) is None


def test_a_page_without_text_under_a_big_image_is_unreadable():
    """이 판정이 나중에 외부 Vision을 켤지 정하는 근거다."""
    covered = ParsedPage(number=1, text="", visible_chars=0, image_area_ratio=0.9)
    thin = ParsedPage(number=2, text="", visible_chars=0, image_area_ratio=0.05)
    wordy = ParsedPage(number=3, text="본문" * 60, visible_chars=120, image_area_ratio=0.9)

    assert covered.unreadable
    # 그림이 작으면 그냥 빈 쪽이다. Vision이 읽어 줄 것도 없다.
    assert not thin.unreadable
    # 글자가 있으면 로컬로 이미 읽힌 쪽이다.
    assert not wordy.unreadable


def test_nul_characters_are_dropped_before_they_reach_the_column():
    """ToUnicode 맵이 깨진 글리프가 NUL로 나오면 PostgreSQL의 `text`가 그 행을 못 받는다.

    psycopg가 저장 직전에 죽고 그 자리는 DAG의 첨부별 예외 처리 바깥이라, 첨부 하나가
    그 run 전체를 막는다(2026-09-01).
    """
    page = SimpleNamespace(
        rect=pymupdf.Rect(0, 0, 100, 100),
        rotation=0,
        find_tables=lambda: SimpleNamespace(tables=[]),
        get_text=lambda kind, sort=False: [(0.0, 0.0, 10.0, 10.0, "영업이익\x00 1,200\x00", 0, 0)],
        get_image_info=list,
    )

    read = read_page(page, 1)

    assert read.text == "영업이익 1,200"
    # NUL을 뺀 뒤 센다. 안 그러면 NUL뿐인 쪽이 글자가 있는 쪽으로 보여 unreadable을 못 넘긴다.
    assert read.visible_chars == len("영업이익 1,200")
    # 표 셀은 블록이 아니라 격자에서 따로 오므로 그쪽도 지운다.
    assert "\x00" not in markdown_table([["구분", "값"], ["영업이익", "1,2\x0000"]])


def test_a_real_pdf_gives_page_markers_and_one_copy_of_the_table(tmp_path):
    """표 셀의 글자를 본문과 표에 두 번 담지 않는다."""
    digest = report_pdf(tmp_path / "probe.pdf")

    result = AttachmentPdfParser(tmp_path).parse(candidate())

    assert result.status == "ok"
    # 저장이 이 값으로 행의 sha256을 대조한다. 파싱 전에 따로 대조하지 않는다.
    assert result.source_sha256 == digest
    assert result.page_count == 2
    assert result.unreadable_page_count == 0
    assert result.parser_version == PARSER_VERSION
    assert result.text is not None
    assert result.text.startswith("<!-- page:1 -->")
    assert "<!-- page:2 -->" in result.text
    assert "한국은행 금융통화위원회는 기준금리를 동결했다." in result.text
    assert "| 구분 | 2025 | 2026(E) |" in result.text
    assert "| 영업이익 | 1,200 | 1,450 |" in result.text
    # 셀 글자가 본문 블록으로 한 번 더 들어오면 이 수가 늘어난다.
    assert result.text.count("1,200") == 1


def test_an_image_only_page_is_counted_and_never_rendered(tmp_path):
    """스캔 쪽은 로컬로 할 수 있는 것이 없다. 세기만 한다."""
    path = tmp_path / "scan.pdf"
    document = pymupdf.open()
    page = document.new_page()
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 500))
    pixmap.set_rect(pixmap.irect, (200, 200, 200))
    page.insert_image(page.rect, pixmap=pixmap)
    document.save(path)
    document.close()

    result = AttachmentPdfParser(tmp_path).parse(candidate("scan.pdf"))

    assert result.status == "ok"
    assert result.page_count == 1
    assert result.unreadable_page_count == 1
    # 글자가 없으므로 저장할 본문도 없다.
    assert result.text is None
    # 렌더링하지 않으므로 디스크에 새 파일이 없다.
    assert sorted(item.name for item in tmp_path.iterdir()) == ["scan.pdf"]


def test_an_encrypted_pdf_is_settled_as_unsupported(tmp_path):
    path = tmp_path / "locked.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((60, 70), "비밀", fontname=FONT, fontsize=11)
    document.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    document.close()

    result = AttachmentPdfParser(tmp_path).parse(candidate("locked.pdf"))

    # 다시 열어도 같은 답이라 확정하고 큐에서 뺀다.
    assert result.status == "unsupported"
    assert result.text is None


def test_a_settled_bad_file_carries_why_it_could_not_be_read(tmp_path):
    """파일 문제인지 파서 문제인지 가르려면 사유가 로그가 아니라 결과에 있어야 한다."""
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7 this is not really a pdf")

    result = AttachmentPdfParser(tmp_path).parse(candidate("broken.pdf"))

    assert result.status == "failed"
    # 제공처가 낸 문장을 그대로 싣는다. 뭉개면 무엇이 났는지 가릴 단서가 사라진다.
    assert result.reason is not None
    assert "as type pdf" in result.reason


def test_an_encrypted_file_says_so_in_its_reason(tmp_path):
    path = tmp_path / "locked.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((60, 70), "비밀", fontname=FONT, fontsize=11)
    document.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    document.close()

    result = AttachmentPdfParser(tmp_path).parse(candidate("locked.pdf"))

    assert result.status == "unsupported"
    assert result.reason == "the file is password protected"


def test_a_readable_file_has_no_reason(tmp_path):
    report_pdf(tmp_path / "probe.pdf")

    assert AttachmentPdfParser(tmp_path).parse(candidate()).reason is None


def test_a_file_that_is_not_a_pdf_is_settled_as_failed(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.7 this is not really a pdf")

    result = AttachmentPdfParser(tmp_path).parse(candidate("broken.pdf"))

    assert result.status == "failed"
    assert result.text is None


def test_a_missing_file_raises_so_the_next_run_picks_it_up_again(tmp_path):
    """되돌릴 수 있는 실패에는 상태를 남기지 않는다."""
    with pytest.raises(FileNotFoundError):
        AttachmentPdfParser(tmp_path).parse(candidate("gone.pdf"))


def test_storing_fills_the_columns_the_model_has():
    """수집기는 문자열 SQL을 쓴다. 모델과 어긋나면 저장 시점에야 안다."""
    table: Table = AttachmentModel.__table__
    assigned = updated_columns(PARSE_UPDATE)

    assert set(assigned) <= set(table.columns.keys())
    assert {"parse_status", "extracted_text", "parsed_sha256", "parser_version", "parsed_at"} <= set(assigned)


def test_storing_guards_on_the_sha_it_read():
    """파일이 그 사이 바뀌었으면 우리가 만든 텍스트는 다른 문서의 것이다."""
    connection = FakeConnection()
    result = ParsedAttachment(
        attachment_id=7,
        status="ok",
        text="<!-- page:1 -->\n본문",
        source_sha256="abc",
        page_count=3,
        unreadable_page_count=1,
    )

    assert AttachmentPdfParser.store(connection, result) == 1

    statement, parameters = connection.recorded_cursor.calls[0]
    assert statement is PARSE_UPDATE
    assert parameters == {
        "parse_status": "ok",
        "extracted_text": "<!-- page:1 -->\n본문",
        "parsed_sha256": "abc",
        "parser_version": PARSER_VERSION,
        "page_count": 3,
        "unreadable_page_count": 1,
        "attachment_id": 7,
    }
    assert "AND sha256 = %(parsed_sha256)s" in statement


def test_the_queue_asks_only_for_pdf_files_it_has_not_read():
    """`parsed_sha256 IS DISTINCT FROM sha256`이 곧 백필이고, 파일이 바뀐 첨부와 파서 판이 오른
    첨부도 같은 줄에 선다."""
    statement = without_comments(PENDING_PARSES)

    assert "kind = 'file'" in statement
    assert "parsed_sha256 IS DISTINCT FROM a.sha256" in statement
    assert "parser_version IS DISTINCT FROM %(parser_version)s" in statement
    assert "%%.pdf" in statement
    assert "LIMIT %(limit)s" in statement


def test_pending_attachments_reads_the_rows_into_models():
    connection = FakeConnection([(7, "documents/boj/1042/0.pdf")])

    waiting = pending_attachments(connection, 50)

    assert waiting == (ParseCandidate(id=7, storage_path="documents/boj/1042/0.pdf"),)
    assert connection.recorded_cursor.calls[0][1] == {"limit": 50, "parser_version": PARSER_VERSION}


def updated_columns(statement: str) -> tuple[str, ...]:
    body = re.search(r"SET (.+?)\nWHERE", statement, re.DOTALL)
    assert body is not None
    return tuple(match.group(1) for match in re.finditer(r"(\w+) =", body.group(1)))


def without_comments(statement: str) -> str:
    """설명 주석을 뗀 SQL. 주석이 규칙을 글로 적고 있어 그대로 찾으면 늘 걸린다."""
    return re.sub(r"(?m)^\s*--.*$", "", statement)


# --- 읽는 순서와 프레임 ---------------------------------------------------

PAGE = pymupdf.Rect(0, 0, 600, 800)


def piece(text: str, x0: float, y0: float, width: float = 200, height: float = 12, margin: bool = False) -> PagePiece:
    return PagePiece(text=text, x0=x0, y0=y0, x1=x0 + width, y1=y0 + height, in_margin=margin)


def test_a_two_column_page_reads_the_left_column_first():
    """y축 하나로 정렬하면 왼쪽 본문과 오른쪽 차트 라벨이 줄 단위로 섞인다(2026-09-01 실측)."""
    pieces = [
        piece("본문 첫 줄", 40, 100),
        piece("(GWh)", 380, 105),
        piece("본문 둘째 줄", 40, 120),
        piece("300", 380, 125),
    ]

    assert [item.text for item in reading_order(pieces, PAGE)] == [
        "본문 첫 줄",
        "본문 둘째 줄",
        "(GWh)",
        "300",
    ]


def test_a_full_width_piece_splits_the_columns_into_bands():
    """전폭 제목을 무시하면 제목 아래 왼쪽 단이 제목 위 오른쪽 단보다 먼저 나온다."""
    pieces = [
        piece("위 왼쪽", 40, 100),
        piece("위 오른쪽", 380, 100),
        piece("전폭 제목", 40, 200, width=520),
        piece("아래 왼쪽", 40, 300),
        piece("아래 오른쪽", 380, 300),
    ]

    assert [item.text for item in reading_order(pieces, PAGE)] == [
        "위 왼쪽",
        "위 오른쪽",
        "전폭 제목",
        "아래 왼쪽",
        "아래 오른쪽",
    ]


def test_a_single_column_page_keeps_the_y_axis_order():
    """빈 띠가 없으면 예전과 같다. 1단 조판을 흔들지 않는 것이 이 규칙의 조건이다."""
    pieces = [piece("첫 문단", 40, 100, width=520), piece("둘째 문단", 40, 140, width=520)]

    assert column_gutter(pieces, PAGE) is None
    assert [item.text for item in reading_order(pieces, PAGE)] == ["첫 문단", "둘째 문단"]


def test_one_stray_piece_on_the_right_is_not_a_second_column():
    """오른쪽에 주석 한 조각뿐인 1단 페이지를 2단으로 읽으면 그 주석이 본문 끝으로 밀린다."""
    pieces = [piece("본문 첫 줄", 40, 100), piece("본문 둘째 줄", 40, 120), piece("주1)", 380, 110, width=40)]

    assert column_gutter(pieces, PAGE) is None


def test_the_gutter_is_only_looked_for_in_the_middle_of_the_page():
    """가장자리의 빈 띠는 여백이지 단 경계가 아니다."""
    pieces = [piece("들여쓴 본문", 300, 100, width=260), piece("들여쓴 둘째 줄", 300, 120, width=260)]

    assert column_gutter(pieces, PAGE) is None


def test_page_numbers_fold_into_one_frame_line():
    """쪽 번호는 쪽마다 값이 달라 숫자를 접어야 같은 줄로 세어진다."""
    assert normalize_frame("- 12 -") == normalize_frame("- 3 -") == "- # -"
    assert normalize_frame("2026년  9월\n한국은행") == "#년 #월 한국은행"


def test_a_line_repeated_in_the_margin_is_dropped_from_every_page():
    pages = [
        ParsedPage(
            number=number,
            text="머리글\n\n본문",
            visible_chars=6,
            image_area_ratio=0.0,
            pieces=(piece("머리글", 40, 10, margin=True), piece("본문", 40, 400)),
        )
        for number in (1, 2, 3)
    ]

    assert running_frame(pages) == frozenset({"머리글"})
    trimmed = drop_running_frame(pages)

    assert [page.text for page in trimmed] == ["본문", "본문", "본문"]
    # 본문이 없는 스캔 쪽이 머리글 글자 수만으로 unreadable 문턱을 넘으면 안 된다.
    assert [page.visible_chars for page in trimmed] == [3, 3, 3]


def test_a_one_page_document_never_loses_anything():
    """되풀이가 조건이라 한 쪽짜리는 프레임 판정 자체가 성립하지 않는다."""
    page = ParsedPage(
        number=1,
        text="한국은행\n\n본문",
        visible_chars=7,
        image_area_ratio=0.0,
        pieces=(piece("한국은행", 40, 10, margin=True), piece("본문", 40, 400)),
    )

    assert running_frame([page]) == frozenset()
    assert drop_running_frame([page])[0].text == "한국은행\n\n본문"


def test_body_text_in_the_middle_of_the_page_survives_even_when_it_repeats():
    """여백 띠 밖의 되풀이는 프레임이 아니다 — 정형 문구가 본문에 있을 수 있다."""
    pages = [
        ParsedPage(
            number=number,
            text="같은 문장",
            visible_chars=5,
            image_area_ratio=0.0,
            pieces=(piece("같은 문장", 40, 400),),
        )
        for number in (1, 2, 3, 4)
    ]

    assert running_frame(pages) == frozenset()


def test_a_real_report_drops_its_running_header_and_page_numbers(tmp_path):
    """머리글·쪽 번호가 있는 세 쪽 PDF. 본문과 한 쪽뿐인 제목은 남는다."""
    path = tmp_path / "framed.pdf"
    document = pymupdf.open()
    for number in range(1, 4):
        page = document.new_page()
        top = page.rect.height * MARGIN_BAND_RATIO
        page.insert_text((60, top - 6), "한화리서치 | 은행 (Positive)", fontname=FONT, fontsize=9)
        page.insert_text((60, 300), f"{number}쪽 본문이다. 실적은 개선됐다.", fontname=FONT, fontsize=11)
        page.insert_text((280, page.rect.height - 12), f"- {number} -", fontname=FONT, fontsize=9)
    document.save(path)
    document.close()

    result = AttachmentPdfParser(tmp_path).parse(candidate("framed.pdf"))

    assert result.text is not None
    assert "한화리서치" not in result.text
    assert "- 1 -" not in result.text and "- 3 -" not in result.text
    assert result.text.count("본문이다. 실적은 개선됐다.") == 3


def test_a_one_character_strip_at_the_edge_is_an_index_tab():
    """`1 / 정 / 책`처럼 가장자리에 세로로 쌓은 색인 탭. 본문이 아니라 조판 장치다."""
    rect = pymupdf.Rect(0, 0, 595, 842)

    # 오른쪽 끝의 한 글자 폭 띠.
    assert side_strip(557.8, 566.2, rect, 0)
    # 왼쪽 끝도 같다.
    assert side_strip(20.0, 28.4, rect, 0)

    # 본문은 넓어서 안 걸린다.
    assert not side_strip(63.4, 252.9, rect, 0)
    # 좁아도 가운데면 차트 눈금이지 탭이 아니다.
    assert not side_strip(300.0, 308.0, rect, 0)
    # 문턱 바로 위 폭은 그대로 둔다.
    assert not side_strip(560.0, 560.0 + 595 * SIDE_STRIP_MAX_WIDTH, rect, 0)
    # 돌아간 페이지에서는 좌우 끝이 표의 첫 열·끝 열이다. 같은 규칙이 본문을 지운다.
    assert not side_strip(557.8, 566.2, rect, 90)


def test_index_tabs_never_reach_the_extracted_text(tmp_path):
    """탭은 y가 본문 사이사이라, 안 떼면 문단 하나 걸러 하나씩 끼어든다."""
    path = tmp_path / "tabbed.pdf"
    document = pymupdf.open()
    page = document.new_page()
    for order, (tab, top) in enumerate([("1", 90), ("정", 105), ("책", 120)]):
        page.insert_text((page.rect.x1 - 30, top), tab, fontname=FONT, fontsize=9)
    page.insert_text((70, 100), "본문 첫 문단이다.", fontname=FONT, fontsize=11)
    page.insert_text((70, 130), "본문 둘째 문단이다.", fontname=FONT, fontsize=11)
    document.save(path)
    document.close()

    result = AttachmentPdfParser(tmp_path).parse(candidate("tabbed.pdf"))

    assert result.text is not None
    assert "본문 첫 문단이다." in result.text
    assert "본문 둘째 문단이다." in result.text
    # 되풀이가 조건이 아니므로 한 쪽짜리 문서에서도 떨어진다.
    assert "정" not in result.text.replace("본문 첫 문단이다.", "").replace("본문 둘째 문단이다.", "")
