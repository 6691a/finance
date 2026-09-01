"""받아 둔 첨부 PDF에서 텍스트와 표를 뽑는다.

`body.py`가 문서의 본문과 첨부 **파일**을 받고, 여기가 그 파일 안의 글자를 꺼낸다.
설계는 `docs/analysis/pdf-parsing-bm25.md`다.

## 외부로 나가지 않는다

네트워크 클라이언트도 API 키도 없다. 페이지를 이미지로 렌더링하지도, 크롭을 만들지도
않으므로 디스크에 새로 쌓이는 파일이 없다. 글자가 안 나오는 페이지는 **건수만 센다** —
그 비율이 나중에 외부 Vision을 켤지 정하는 유일한 근거다
(`docs/analysis/pdf-vision-analysis.md` 5절).

## 표를 두 번 담지 않는다

`get_text()`는 표 셀의 글자도 본문에 섞어 준다. 거기에 표를 Markdown으로 한 번 더 붙이면
같은 숫자가 두 벌이 되어 색인과 프롬프트가 함께 부푼다. 그래서 **블록 단위로 읽고**
(`get_text("blocks")`), 쓸 만한 표의 bbox 안에 들어가는 블록은 버린 자리에 Markdown 표를
끼운다. 순서는 y축이다 — 1단 조판 기준이고, 2단 조판은 두 단이 줄 단위로 섞인다(BM25에는
무관하고 프롬프트에만 영향이 있다).

**쓸 만한 표의 기준은 셋이다**(`usable_grid`). 채움률, 최소 행·열, 숫자 셀. 셋을 못 넘긴
격자는 버리고 페이지 텍스트만 남긴다 — 셀 글자는 이미 그 안에 있으므로 **잃는 것은 행·열
구조뿐이다.** 그 구조가 필요해지는 경우가 Vision을 켜는 조건 ②다.

## 실패

- 열리지 않는 파일(손상·PDF 아님)은 `failed`, 암호가 걸렸으면 `unsupported`로 **확정**한다.
  다시 열어도 같은 답이다.
- 페이지 일부가 실패하면 `partial`로 저장하고 **읽은 만큼은 남긴다.** 일부라도 검색에
  걸리는 편이 아무 것도 못 찾는 것보다 낫다.
- 파일이 없거나 읽다가 I/O가 죽으면 예외를 그대로 올린다. **상태를 남기지 않아** 다음
  실행이 다시 집는다. 되돌릴 수 있는 실패에 상태를 남기면 재시도 규칙을 따로 써야 한다.
- 디스크의 파일이 행의 `sha256`과 다르면 `store`의 UPDATE가 0행이다. 파싱 전에 따로 대조하지
  않는다 — 같은 조건을 두 번 보는 것이고, 행이 이미 새 SHA로 갱신돼 있으면 그 자리에서 맞는
  텍스트가 저장된다(설계 15절 ⑤).
"""

import hashlib
import logging
import re
from pathlib import Path

import pymupdf
from pydantic import BaseModel, ConfigDict

from modules.db import Connection
from modules.sql import read_sql

logger = logging.getLogger(__name__)

# 컨테이너가 첨부를 두는 자리. `document_attachment.storage_path`가 이 아래의 상대경로다.
DEFAULT_FILE_ROOT = Path("/opt/airflow/files")

# 파서 이름과 판. **규칙이나 아래 상수가 바뀌면 올린다** — 어느 첨부를 다시 파싱할지
# 판정하는 값이 이것이다.
PARSER_VERSION = "pymupdf/1"

# 표로 확정하는 기준 셋. **아직 실측으로 확정하지 않은 잠정값이다**(설계 4.2·10.1).
MIN_TABLE_FILL = 0.6
MIN_TABLE_ROWS = 2
MIN_TABLE_COLUMNS = 2

# 글자가 나오지 않는 페이지의 기준(설계 4.3). 이것도 잠정값이다.
UNREADABLE_MAX_CHARS = 80
UNREADABLE_MIN_IMAGE_RATIO = 0.20

# 셀 하나가 숫자인지. 천 단위 쉼표, 소수점, 부호, 퍼센트까지만 본다.
NUMERIC_CELL = re.compile(r"^[\s(+\-]*[0-9][0-9,.\s]*\)?%?$")

PENDING_PARSES = read_sql("postgres", "document_attachment", "select_pending_parse.sql")
PARSE_UPDATE = read_sql("postgres", "document_attachment", "update_parse.sql")


class ParseCandidate(BaseModel):
    """파싱을 기다리는 첨부 하나."""

    model_config = ConfigDict(frozen=True)

    id: int
    storage_path: str


class ParsedPage(BaseModel):
    """페이지 하나에서 얻은 것."""

    model_config = ConfigDict(frozen=True)

    number: int
    text: str
    visible_chars: int
    image_area_ratio: float

    @property
    def unreadable(self) -> bool:
        """글자가 없고 그림이 페이지를 덮고 있으면 로컬로 할 수 있는 것이 없다."""
        return self.visible_chars < UNREADABLE_MAX_CHARS and self.image_area_ratio >= UNREADABLE_MIN_IMAGE_RATIO


class ParsedAttachment(BaseModel):
    """첨부 하나의 파싱 결과. 이대로 한 행이 된다."""

    model_config = ConfigDict(frozen=True)

    attachment_id: int
    status: str
    text: str | None = None
    source_sha256: str
    page_count: int = 0
    unreadable_page_count: int = 0
    parser_version: str = PARSER_VERSION
    # 페이지 단위 실패 사유. 저장하지 않고 태스크 로그로만 올린다.
    failures: tuple[str, ...] = ()


def usable_grid(grid: list[list[str | None]]) -> bool:
    """이 격자를 표로 확정할 수 있나.

    셋을 다 만족해야 한다 — 채움률, 최소 행·열, 숫자 셀 하나. 못 넘긴 격자는 버리고 페이지
    텍스트만 남긴다. 레이아웃용 표(2단 조판의 바깥 틀, 머리글 줄)가 이 문턱에서 걸린다.
    """
    if len(grid) < MIN_TABLE_ROWS:
        return False
    if max((len(row) for row in grid), default=0) < MIN_TABLE_COLUMNS:
        return False

    cells = [cell for row in grid for cell in row]
    if not cells:
        return False
    filled = [cell for cell in cells if cell and cell.strip()]
    if len(filled) / len(cells) < MIN_TABLE_FILL:
        return False
    return any(NUMERIC_CELL.match(cell) for cell in filled)


def markdown_table(grid: list[list[str | None]]) -> str:
    """격자를 Markdown 표로 편다.

    **표기를 하나로 고정하는 것이 이 함수의 목적이다.** 이 텍스트가 그대로 BM25 색인과
    LLM 프롬프트로 가므로, 같은 문서 안에서 표가 두 모양으로 섞이면 검색어와 읽는 쪽이 함께
    흔들린다.
    """
    width = max(len(row) for row in grid)
    rows = [[_cell(row[index] if index < len(row) else None) for index in range(width)] for row in grid]
    header, *body = rows
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * width) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in body)
    return "\n".join(lines)


def _cell(value: str | None) -> str:
    # 줄바꿈과 파이프는 표를 깨뜨린다. 셀 안의 줄바꿈은 공백으로 접는다.
    return (value or "").replace("\n", " ").replace("|", "/").strip()


def pending_attachments(connection: Connection, limit: int) -> tuple[ParseCandidate, ...]:
    """파싱을 기다리는 첨부를 오래된 것부터 집는다. 파서 판이 오른 첨부도 같은 줄에 선다."""
    with connection.cursor() as cursor:
        cursor.execute(PENDING_PARSES, {"limit": limit, "parser_version": PARSER_VERSION})
        rows = cursor.fetchall()
    return tuple(ParseCandidate(id=row[0], storage_path=row[1]) for row in rows)


class AttachmentPdfParser:
    """첨부 파일 하나를 열어 텍스트를 만든다.

    생성자가 받는 것은 **그 실행 동안 안 변하는 것 하나**(파일 뿌리)다. 첨부는 메서드 인자다.
    """

    def __init__(self, file_root: Path = DEFAULT_FILE_ROOT) -> None:
        self._file_root = file_root

    def parse(self, candidate: ParseCandidate) -> ParsedAttachment:
        """첨부 하나를 읽어 결과를 돌려준다. DB에 쓰지 않는다.

        읽은 파일의 SHA를 결과에 싣는다. 그것이 행의 `sha256`과 같을 때만 `store`가 갱신한다 —
        내용을 정확히 읽어도 그것이 이 문서에 붙었던 파일이라는 보장은 그 대조가 준다.
        """
        path = self._file_root / candidate.storage_path
        digest = hashlib.sha256(path.read_bytes()).hexdigest()

        try:
            document = pymupdf.open(path)
        except (pymupdf.FileDataError, ValueError) as error:
            logger.warning("attachment %s could not be opened: %s", candidate.id, error)
            return ParsedAttachment(attachment_id=candidate.id, status="failed", source_sha256=digest)

        with document:
            if document.needs_pass:
                return ParsedAttachment(attachment_id=candidate.id, status="unsupported", source_sha256=digest)
            pages, failures = self._pages(document, candidate)
            page_count = document.page_count

        if failures and not pages:
            return ParsedAttachment(
                attachment_id=candidate.id,
                status="failed",
                source_sha256=digest,
                page_count=page_count,
                failures=tuple(failures),
            )

        return ParsedAttachment(
            attachment_id=candidate.id,
            status="partial" if failures else "ok",
            text=assemble(pages),
            source_sha256=digest,
            page_count=page_count,
            unreadable_page_count=sum(1 for page in pages if page.unreadable),
            failures=tuple(failures),
        )

    def _pages(self, document: "pymupdf.Document", candidate: ParseCandidate) -> tuple[list[ParsedPage], list[str]]:
        pages: list[ParsedPage] = []
        failures: list[str] = []
        for number, page in enumerate(document, start=1):
            try:
                pages.append(read_page(page, number))
            except (pymupdf.FileDataError, RuntimeError, ValueError) as error:
                # 페이지 하나가 깨져도 나머지는 읽는다. 상태는 `partial`이 된다.
                logger.warning("attachment %s page %s failed: %s", candidate.id, number, error)
                failures.append(f"page {number}({error})")
        return pages, failures

    @staticmethod
    def store(connection: Connection, result: ParsedAttachment) -> int:
        """결과를 첨부 행에 채우고 갱신한 행 수를 돌려준다(0 또는 1).

        0이면 그 사이 파일이 바뀌어 SHA가 어긋난 것이다. 커밋은 부르는 쪽이 한다.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                PARSE_UPDATE,
                {
                    "parse_status": result.status,
                    "extracted_text": result.text,
                    "parsed_sha256": result.source_sha256,
                    "parser_version": result.parser_version,
                    "page_count": result.page_count,
                    "unreadable_page_count": result.unreadable_page_count,
                    "attachment_id": result.attachment_id,
                },
            )
            return max(cursor.rowcount, 0)


def read_page(page: "pymupdf.Page", number: int) -> ParsedPage:
    """페이지 하나를 텍스트 블록과 표로 읽는다."""
    # `extract()`는 셀 텍스트를 다시 읽는다. 표마다 한 번만 부르고 판정과 렌더가 같은 격자를 쓴다.
    grids = [(table, table.extract()) for table in page.find_tables().tables]
    tables = [(pymupdf.Rect(table.bbox), grid) for table, grid in grids if usable_grid(grid)]
    table_boxes = [box for box, _ in tables]

    pieces: list[tuple[float, str]] = []
    visible = 0
    for block in page.get_text("blocks", sort=True):
        x0, y0, x1, y1, text, _, block_type = block
        if block_type != 0:
            # 이미지 블록이다. 좌표는 아래 면적 판정이 따로 본다.
            continue
        visible += len(text.strip())
        if any(_inside(pymupdf.Rect(x0, y0, x1, y1), box) for box in table_boxes):
            # 표 안의 글자다. 아래에서 Markdown 표로 한 번만 싣는다.
            continue
        if text.strip():
            pieces.append((y0, text.strip()))

    for box, grid in tables:
        pieces.append((box.y0, markdown_table(grid)))

    pieces.sort(key=lambda piece: piece[0])
    return ParsedPage(
        number=number,
        text="\n\n".join(piece[1] for piece in pieces),
        visible_chars=visible,
        image_area_ratio=image_area_ratio(page),
    )


def image_area_ratio(page: "pymupdf.Page") -> float:
    """그림이 페이지의 얼마를 덮고 있나.

    **가장 큰 그림 하나가 아니라 합이다.** 작은 조각 여럿이 페이지 한 장을 이루는 경우가
    있다. 이미지 바이너리는 읽지 않는다 — 면적 판정에 필요 없다.
    """
    area = abs(page.rect.get_area())
    if not area:
        return 0.0
    covered = sum(abs(pymupdf.Rect(info["bbox"]).get_area()) for info in page.get_image_info())
    return min(covered / area, 1.0)


def assemble(pages: list[ParsedPage]) -> str | None:
    """페이지들을 첨부 하나의 본문으로 잇는다.

    **페이지 표식을 남긴다.** 검색 결과에서 원문 몇 쪽인지 되짚는 유일한 단서다.
    글자가 한 자도 없으면 `None`이다 — 빈 문자열을 저장하면 "읽었는데 비었다"와 "안 읽었다"가
    같아 보인다.
    """
    blocks = [f"<!-- page:{page.number} -->\n{page.text}" for page in pages if page.text]
    return "\n\n".join(blocks) if blocks else None


def _inside(block: "pymupdf.Rect", box: "pymupdf.Rect") -> bool:
    # 블록의 중심이 표 안이면 그 표의 글자로 본다. 경계에 걸친 블록까지 버리지 않는다.
    center = pymupdf.Point((block.x0 + block.x1) / 2, (block.y0 + block.y1) / 2)
    return bool(box.contains(center))
