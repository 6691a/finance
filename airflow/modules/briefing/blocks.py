"""세 리포트가 함께 쓰는 Block Kit 조각.

값은 Slack 기본 `table` 블록에 넣는다. **열을 우리가 맞추지 않는다.** 코드 블록에 공백을
채워 맞추던 방식은 한글에서 어긋났다 — 코드 블록 글꼴(`Monaco`/`Menlo` 계열)에 한글이 없어
대체 글꼴로 떨어지고 그 자간이 ASCII의 정확히 두 배가 아니라, 칸 수를 아무리 정확히 세어도
한글 수가 다른 줄끼리 밀린다. `table` 블록은 클라이언트가 열을 맞추므로 그 문제가 없다.
"""

from collections.abc import Sequence
from datetime import datetime
from typing import Any

# Slack의 한 블록 텍스트 상한은 3000자다. 넘으면 메시지 전체가 거절되므로 여기서 자른다.
MAX_SECTION_CHARS = 2900

# 요일 표기. `strftime("%a")`는 실행 환경의 `LC_TIME`을 타서 컨테이너 로케일이 바뀌면
# 조용히 `Tue`가 된다. 표를 직접 둔다.
WEEKDAY_NAMES = ("월", "화", "수", "목", "금", "토", "일")


def timestamp(local: datetime) -> str:
    """`08/18(화) 12:30 KST`. 세 리포트의 헤더가 같은 모양을 쓴다."""
    return f"{local:%m/%d}({WEEKDAY_NAMES[local.weekday()]}) {local:%H:%M} KST"


def header(text: str) -> dict[str, Any]:
    """header 블록은 이모지와 평문만 받는다. mrkdwn이 아니다."""
    return {"type": "header", "text": {"type": "plain_text", "text": text[:150], "emoji": True}}


def section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:MAX_SECTION_CHARS]}}


def table_section(title: str, headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[dict[str, Any]]:
    """제목 `section` 하나와 `table` 블록 하나.

    `table` 블록에는 제목 칸이 없어 두 블록으로 나눈다. 첫 열은 이름이라 좌측 정렬에
    줄바꿈을 허용하고 나머지는 숫자라 우측 정렬이다. 자릿수가 세로로 맞아야 눈으로 비교된다.
    """
    return [
        section(f"*{title}*"),
        {
            "type": "table",
            "column_settings": [{"is_wrapped": True}, *({"align": "right"} for _ in headers[1:])],
            "rows": [_row(headers), *(_row(row) for row in rows)],
        },
    ]


def _row(cells: Sequence[str]) -> list[dict[str, Any]]:
    """`raw_text` 칸. mrkdwn이 아니라 링크나 굵게는 들어가지 않는다."""
    return [{"type": "raw_text", "text": cell} for cell in cells]


def divider() -> dict[str, Any]:
    return {"type": "divider"}


def context(lines: Sequence[str]) -> dict[str, Any]:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": " · ".join(lines)[:MAX_SECTION_CHARS]}]}


def comment_blocks(comment: str | None, error: str | None = None) -> list[dict[str, Any]]:
    """요약 블록. LLM이 실패해도 리포트는 나가지만 **실패했다는 사실은 채널에 남긴다.**

    로그만 남기고 조용히 빠지면 요약이 원래 없는 리포트와 구분되지 않는다. 그 상태가
    며칠 이어져도 아무도 모른다.
    """
    if comment:
        return [divider(), section(f"💬 {comment}")]
    if error:
        return [divider(), context([f"⚠️ 요약 생성 실패: {error}"])]
    return []
