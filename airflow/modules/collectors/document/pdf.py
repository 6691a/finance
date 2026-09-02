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
끼운다.

**쓸 만한 표의 기준은 넷이다**(`usable_grid`). 채움률, 최소 행·열, 숫자 셀, 셀 길이. 넷을
못 넘긴 격자는 버리고 페이지 텍스트만 남긴다 — 셀 글자는 이미 그 안에 있으므로 **잃는 것은
행·열 구조뿐이다.** 그 구조가 필요해지는 경우가 Vision을 켜는 조건 ②다.

넷째(셀 길이)가 막는 것은 **표를 찾은 실패**다. 열 경계를 실제보다 적게 찾으면 여러 칸이 한
칸에 뭉치는데, 그때도 앞의 셋은 통과한다. 그 격자를 채택하면 **원본 블록을 버린 자리에 뭉개진
표가 들어가** 페이지 텍스트가 원본보다 나빠진다. 버리는 쪽은 구조만 잃고 글자는 남으므로
**비용이 한쪽으로 기울어 있다** — 그래서 애매하면 버린다.

## 읽는 순서는 단을 먼저 본다

y축 하나로 정렬하면 2단 조판에서 두 단이 줄 단위로 섞인다 — 왼쪽 본문 한 줄과 오른쪽 차트
라벨 한 줄이 번갈아 나와 문장이 쪼개진다(2026-09-01 실측). 그래서 **세로 빈 띠(gutter)를
찾아 단을 가르고**(`reading_order`) 왼쪽 단을 다 읽은 뒤 오른쪽 단을 읽는다. 빈 띠가 없으면
1단이고 예전과 같은 y축 순서다.

**단을 가로지르는 조각은 띠를 가른다.** 전폭 제목이나 전폭 표 위아래의 단은 서로 다른
덩어리라, 그것을 무시하고 페이지 전체를 두 단으로 읽으면 제목 아래 왼쪽 단이 제목 위
오른쪽 단보다 먼저 나온다.

## 머리글·바닥글은 문서 단위로 뗀다

쪽마다 되풀이되는 머리글·바닥글·쪽 번호는 본문이 아니다. BM25에서는 같은 문자열이 문서
전체에 깔려 잡음이 되고, LLM 프롬프트에서는 토큰만 먹는다.

**한 쪽만 보고는 그것이 프레임인지 그 쪽의 제목인지 알 수 없다.** 그래서 판정은 문서
단위다(`running_frame`) — 위아래 여백 띠에 있고 **여러 쪽에 되풀이되는** 줄만 뗀다. 쪽
번호는 쪽마다 값이 달라 숫자를 `#`으로 접은 뒤 센다. 되풀이가 조건이므로 **1쪽짜리 문서는
아무 것도 잃지 않는다.**

## 가장자리 색인 탭은 모양으로 뗀다

`1 / 정 / 책`처럼 **한 글자씩 세로로 쌓아 가장자리에 붙인 색인 탭**이 있다. 글자는 회전하지
않았고 y가 본문 사이사이라, 정렬하면 문단 하나 걸러 하나씩 끼어든다.

**이것만 되풀이를 안 세고 모양으로 판정한다**(`side_strip`). 탭은 본문 쪽에만 있어서
표지·목차·부록이 섞인 문서에서는 프레임 문턱(쪽 수의 50%)을 못 넘는다 — 실측한 문서가
29쪽 중 13쪽(44.8%)이었다. 대신 **폭이 한 글자이고 좌우 끝에 붙어 있다**는 조건이 그 자체로
좁아서 본문을 지울 위험이 낮다.

**페이지가 돌아가 있으면(`page.rotation`) 보지 않는다.** 그때 좌우 끝은 눈에 보이는 여백이
아니라 표의 첫 열·끝 열이다.

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
from collections import Counter
from math import ceil
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
PARSER_VERSION = "pymupdf/4"

# 표로 확정하는 기준 넷. 앞의 셋은 **아직 실측으로 확정하지 않은 잠정값이다**(설계 4.2·10.1).
MIN_TABLE_FILL = 0.6
MIN_TABLE_ROWS = 2
MIN_TABLE_COLUMNS = 2

# 셀 하나가 담을 수 있는 글자 수. 넘으면 열 경계를 실제보다 적게 찾아 여러 칸이 한 칸에
# 뭉친 것이다. **이 값만 실측으로 정했다**(2026-09-01, 저장소의 첨부 983건·12,323쪽).
# 채택된 표 2,331개의 "가장 긴 셀"은 p50이 39자, p75가 84자다. 뭉개진 표는 그 자리에 행이
# 통째로 들어간다 — 17열 ETF 표가 5열로 잡힌 사례가 125자, BEA 산업 분류표가 2,911자였다.
MAX_TABLE_CELL_CHARS = 100

# 단을 가르는 세로 빈 띠를 찾는 구간(페이지 폭의 비율). 가운데 40%만 본다 — 가장자리의
# 빈 띠는 여백이지 단 경계가 아니다.
COLUMN_SEARCH_MIN = 0.30
COLUMN_SEARCH_MAX = 0.70

# 빈 띠가 페이지 폭의 이만큼은 돼야 단 경계다. 그 아래는 낱말 사이 간격이다.
MIN_GUTTER_RATIO = 0.02

# 페이지 폭의 이만큼을 넘게 차지하는 조각은 단 경계를 찾을 때 빼고 본다. 전폭 제목 하나가
# 빈 띠를 통째로 덮어 2단 페이지가 1단으로 보이는 것을 막는다.
FULL_WIDTH_RATIO = 0.60

# 양쪽 단에 각각 이만큼은 있어야 2단으로 본다. 오른쪽에 주석 한 조각뿐인 1단 페이지를
# 2단으로 읽으면 그 주석이 본문 끝으로 밀린다.
MIN_COLUMN_PIECES = 2

# 머리글·바닥글을 찾는 위아래 여백 띠(페이지 높이의 비율).
MARGIN_BAND_RATIO = 0.08

# 가장자리 색인 탭의 모양. 폭이 페이지 폭의 `SIDE_STRIP_MAX_WIDTH`보다 좁고 좌우 바깥
# `SIDE_BAND_RATIO` 띠 안에 통째로 들어 있으면 본문이 아니라 조판 장치다.
# **되풀이를 세지 않는다** — 탭은 본문 쪽에만 있어 표지·목차가 섞인 문서에서 문턱을 못 넘는다
# (SPRi AI Brief 8월호: 29쪽 중 13쪽, 44.8%로 프레임 문턱 50% 미달. 2026-09-01 실측).
SIDE_STRIP_MAX_WIDTH = 0.03
SIDE_BAND_RATIO = 0.10

# 그 띠의 같은 줄이 몇 쪽에 나와야 프레임인가. 쪽 수의 비율과 절대 하한을 함께 본다 —
# 하한이 2라서 **1쪽짜리 문서는 아무 것도 잃지 않는다.**
FRAME_MIN_PAGES = 2
FRAME_MIN_RATIO = 0.5

# 쪽 번호는 쪽마다 값이 다르다. 숫자를 접어야 같은 줄로 세어진다.
FRAME_DIGITS = re.compile(r"\d+")
FRAME_SPACES = re.compile(r"\s+")

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


class PagePiece(BaseModel):
    """페이지 안의 조각 하나 — 문단 블록이거나 Markdown으로 편 표다.

    **좌표를 들고 다니는 이유는 둘이다.** 단을 가르려면 x가, 머리글·바닥글을 가리려면 y가
    있어야 한다. 둘 다 텍스트만으로는 판정할 수 없다. `pymupdf.Rect`가 아니라 float 넷인
    것은 이 모델이 Pydantic이라서다.
    """

    model_config = ConfigDict(frozen=True)

    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    # 위아래 여백 띠에 있나. 프레임 후보라는 뜻이지 프레임이라는 뜻이 아니다 —
    # 되풀이 여부는 문서 단위로 본다. 표는 여백에 걸쳐도 프레임이 아니라 언제나 False다.
    in_margin: bool


class ParsedPage(BaseModel):
    """페이지 하나에서 얻은 것."""

    model_config = ConfigDict(frozen=True)

    number: int
    text: str
    visible_chars: int
    image_area_ratio: float
    # 읽는 순서대로의 조각들. `drop_running_frame`이 문서 전체를 본 뒤 여기서 프레임을 뺀다.
    pieces: tuple[PagePiece, ...] = ()

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

    넷을 다 만족해야 한다 — 채움률, 최소 행·열, 숫자 셀 하나, 셀 길이. 못 넘긴 격자는 버리고
    페이지 텍스트만 남긴다. 레이아웃용 표(2단 조판의 바깥 틀, 머리글 줄)가 앞의 셋에서
    걸리고, **열을 덜 찾아 뭉개진 표가 넷째에서 걸린다.**
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
    if any(len(cell.strip()) > MAX_TABLE_CELL_CHARS for cell in filled):
        # 한 셀에 행이 통째로 들어갔다. 열 경계를 못 찾은 것이라 표가 아니라 뭉친 글자다.
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
    return strip_nul(value or "").replace("\n", " ").replace("|", "/").strip()


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
        return drop_running_frame(pages), failures

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
    """페이지 하나를 텍스트 블록과 표로 읽는다.

    머리글·바닥글은 여기서 떼지 않는다 — 한 쪽만 보고는 프레임인지 그 쪽의 제목인지 가릴 수
    없다. 여백 띠에 있다는 표시만 달아 두고 판정은 `drop_running_frame`이 문서 단위로 한다.
    """
    # `extract()`는 셀 텍스트를 다시 읽는다. 표마다 한 번만 부르고 판정과 렌더가 같은 격자를 쓴다.
    grids = [(table, table.extract()) for table in page.find_tables().tables]
    tables = [(pymupdf.Rect(table.bbox), grid) for table, grid in grids if usable_grid(grid)]
    table_boxes = [box for box, _ in tables]

    band = page.rect.height * MARGIN_BAND_RATIO
    top, bottom = page.rect.y0 + band, page.rect.y1 - band

    pieces: list[PagePiece] = []
    visible = 0
    for block in page.get_text("blocks", sort=True):
        x0, y0, x1, y1, text, _, block_type = block
        if block_type != 0:
            # 이미지 블록이다. 좌표는 아래 면적 판정이 따로 본다.
            continue
        if side_strip(x0, x1, page.rect, page.rotation):
            # 가장자리 색인 탭이다. 본문이 아니라 조판 장치라 글자 수에도 안 넣는다.
            continue
        text = strip_nul(text)
        visible += len(text.strip())
        if any(_inside(pymupdf.Rect(x0, y0, x1, y1), box) for box in table_boxes):
            # 표 안의 글자다. 아래에서 Markdown 표로 한 번만 싣는다.
            continue
        if text.strip():
            pieces.append(PagePiece(text=text.strip(), x0=x0, y0=y0, x1=x1, y1=y1, in_margin=y1 <= top or y0 >= bottom))

    for box, grid in tables:
        pieces.append(PagePiece(text=markdown_table(grid), x0=box.x0, y0=box.y0, x1=box.x1, y1=box.y1, in_margin=False))

    ordered = reading_order(pieces, page.rect)
    return ParsedPage(
        number=number,
        text="\n\n".join(piece.text for piece in ordered),
        visible_chars=visible,
        image_area_ratio=image_area_ratio(page),
        pieces=tuple(ordered),
    )


def reading_order(pieces: list[PagePiece], page_rect: "pymupdf.Rect") -> list[PagePiece]:
    """조각을 사람이 읽는 차례로 늘어놓는다.

    빈 띠가 없으면 y축 순서 그대로다(1단). 있으면 **띠를 가로지르는 조각이 페이지를 가르고**
    각 덩어리 안에서 왼쪽 단을 먼저 읽는다. 전폭 제목 아래의 왼쪽 단이 제목 위의 오른쪽 단
    보다 먼저 나오는 것을 그 가름이 막는다.
    """
    top_down = sorted(pieces, key=lambda piece: (piece.y0, piece.x0))
    gutter = column_gutter(pieces, page_rect)
    if gutter is None:
        return top_down

    ordered: list[PagePiece] = []
    band: list[PagePiece] = []
    for piece in top_down:
        if piece.x0 < gutter < piece.x1:
            ordered.extend(_by_column(band, gutter))
            band = []
            ordered.append(piece)
        else:
            band.append(piece)
    ordered.extend(_by_column(band, gutter))
    return ordered


def _by_column(band: list[PagePiece], gutter: float) -> list[PagePiece]:
    # 왼쪽 단(x1 <= gutter)이 False라 먼저 온다. 이미 y로 정렬돼 있어 단 안의 차례는 유지된다.
    return sorted(band, key=lambda piece: (piece.x1 > gutter, piece.y0))


def column_gutter(pieces: list[PagePiece], page_rect: "pymupdf.Rect") -> float | None:
    """단을 가르는 세로 빈 띠의 x. 1단이면 `None`이다.

    **전폭 조각은 빼고 본다.** 전폭 제목 하나가 빈 띠를 덮으면 2단 페이지가 1단으로 보인다.
    가운데 40% 안의 빈 띠만 후보이고, 양쪽에 조각이 `MIN_COLUMN_PIECES` 이상 있어야 한다 —
    오른쪽에 주석 한 조각뿐인 1단 페이지를 2단으로 읽으면 그 주석이 본문 끝으로 밀린다.
    """
    width = page_rect.width
    if width <= 0:
        return None
    spans = sorted((piece.x0, piece.x1) for piece in pieces if piece.x1 - piece.x0 < width * FULL_WIDTH_RATIO)
    if not spans:
        return None

    low = page_rect.x0 + width * COLUMN_SEARCH_MIN
    high = page_rect.x0 + width * COLUMN_SEARCH_MAX
    best: tuple[float, float] | None = None  # (너비, x)
    cursor = spans[0][1]
    for x0, x1 in spans[1:]:
        gap = x0 - cursor
        middle = cursor + gap / 2
        if gap >= width * MIN_GUTTER_RATIO and low <= middle <= high and (best is None or gap > best[0]):
            best = (gap, middle)
        cursor = max(cursor, x1)
    if best is None:
        return None

    gutter = best[1]
    left = sum(1 for piece in pieces if piece.x1 <= gutter)
    right = sum(1 for piece in pieces if piece.x0 >= gutter)
    return gutter if left >= MIN_COLUMN_PIECES and right >= MIN_COLUMN_PIECES else None


def side_strip(x0: float, x1: float, page_rect: "pymupdf.Rect", rotation: int) -> bool:
    """가장자리에 세로로 쌓은 색인 탭인가.

    한 글자 폭으로 좌우 끝에 붙은 조각이다. 글자는 회전하지 않았고 한 글자씩 줄바꿈으로
    쌓여 있어 회전 판정으로는 안 잡힌다(`SPRi AI Brief`의 `1 / 정 / 책` 탭이 그 예다).
    y가 본문 사이사이라 정렬하면 문단 하나 걸러 하나씩 끼어든다.

    **페이지가 돌아가 있으면 보지 않는다.** 그때는 좌우 끝이 눈에 보이는 여백이 아니라
    표의 첫 열·끝 열이라, 같은 규칙이 본문을 지운다.
    """
    if rotation:
        return False
    width = page_rect.width
    if width <= 0 or x1 - x0 >= width * SIDE_STRIP_MAX_WIDTH:
        return False
    return x1 <= page_rect.x0 + width * SIDE_BAND_RATIO or x0 >= page_rect.x1 - width * SIDE_BAND_RATIO


def normalize_frame(text: str) -> str:
    """프레임 후보를 셀 수 있는 모양으로 접는다. 쪽 번호의 숫자가 `#`이 된다."""
    return FRAME_DIGITS.sub("#", FRAME_SPACES.sub(" ", text).strip())


def running_frame(pages: list[ParsedPage]) -> frozenset[str]:
    """여러 쪽의 여백 띠에 되풀이되는 줄. 머리글·바닥글·쪽 번호다.

    한 쪽 안에서 같은 줄이 두 번 나와도 한 번으로 센다 — 세는 단위는 쪽이다.
    """
    counts: Counter[str] = Counter()
    for page in pages:
        counts.update({normalize_frame(piece.text) for piece in page.pieces if piece.in_margin})
    floor = max(FRAME_MIN_PAGES, ceil(len(pages) * FRAME_MIN_RATIO))
    return frozenset(line for line, count in counts.items() if line and count >= floor)


def drop_running_frame(pages: list[ParsedPage]) -> list[ParsedPage]:
    """문서 전체를 보고 정한 프레임을 각 쪽에서 뺀다.

    **`visible_chars`도 함께 줄인다.** 안 그러면 본문이 한 자도 없는 스캔 쪽이 머리글 글자
    수만으로 `unreadable` 문턱을 넘어 Vision 판정에서 빠진다.
    """
    frame = running_frame(pages)
    if not frame:
        return pages

    trimmed: list[ParsedPage] = []
    for page in pages:
        kept: list[PagePiece] = []
        dropped = 0
        for piece in page.pieces:
            if piece.in_margin and normalize_frame(piece.text) in frame:
                dropped += len(piece.text)
                continue
            kept.append(piece)
        if not dropped:
            trimmed.append(page)
            continue
        trimmed.append(
            page.model_copy(
                update={
                    "pieces": tuple(kept),
                    "text": "\n\n".join(piece.text for piece in kept),
                    "visible_chars": max(page.visible_chars - dropped, 0),
                }
            )
        )
    return trimmed


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


def strip_nul(text: str) -> str:
    """NUL(0x00)을 뺀다.

    **PostgreSQL의 `text`는 NUL을 담지 못한다.** psycopg가 저장 직전 adapt 단계에서
    `ValueError: A string literal cannot contain NUL (0x00) characters`로 죽고, 그 자리는
    DAG의 첨부별 예외 처리 바깥이라 첨부 하나가 그 run 전체를 막았다(2026-09-01).

    NUL은 ToUnicode 맵이 깨진 글리프를 PyMuPDF가 그대로 내보낸 것이라 **글자가 아니다.**
    지워서 잃는 정보가 없고, `visible_chars`를 세기 전에 지워야 NUL만 있는 페이지가
    `unreadable`로 제대로 잡힌다.
    """
    return text.replace("\x00", "")


def _inside(block: "pymupdf.Rect", box: "pymupdf.Rect") -> bool:
    # 블록의 중심이 표 안이면 그 표의 글자로 본다. 경계에 걸친 블록까지 버리지 않는다.
    center = pymupdf.Point((block.x0 + block.x1) / 2, (block.y0 + block.y1) / 2)
    return bool(box.contains(center))
