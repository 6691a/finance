import hashlib
import re
from pathlib import Path
from typing import Self

import pymupdf
import pytest
from sqlalchemy import Table

from apps.models.content import DocumentAttachment as AttachmentModel
from modules.collectors.document.pdf import (
    PARSE_UPDATE,
    PARSER_VERSION,
    PENDING_PARSES,
    AttachmentPdfParser,
    ParseCandidate,
    ParsedAttachment,
    ParsedPage,
    assemble,
    markdown_table,
    pending_attachments,
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
