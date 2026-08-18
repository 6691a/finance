"""Slack 메시지에 넣을 고정폭 표.

Slack mrkdwn에는 표 문법이 없다. 그래서 코드 블록 안에서 공백으로 열을 맞춘다.

**폭은 글자 수가 아니라 표시 칸 수로 센다.** 한글·한자·전각 기호는 고정폭 글꼴에서 두 칸을
차지하므로 `len`으로 맞추면 한글이 섞인 줄만 밀린다. `unicodedata.east_asian_width`가
그 판정을 한다.
"""

import unicodedata
from collections.abc import Sequence

COLUMN_GAP = "  "

# 고정폭 글꼴에서 두 칸을 차지하는 문자 분류. W는 한중일 글자, F는 전각 기호다.
WIDE_CATEGORIES = frozenset({"W", "F"})


def display_width(text: str) -> int:
    """고정폭 글꼴에서 이 문자열이 차지하는 칸 수."""
    return sum(2 if unicodedata.east_asian_width(char) in WIDE_CATEGORIES else 1 for char in text)


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """헤더와 행을 열 맞춰 그린다.

    첫 열은 이름이라 왼쪽 정렬, 나머지는 숫자라 오른쪽 정렬이다. 숫자가 오른쪽으로 붙어야
    자릿수를 눈으로 비교할 수 있다.
    """
    lines = [tuple(headers), *(tuple(row) for row in rows)]
    widths = [max(display_width(line[index]) for line in lines) for index in range(len(headers))]
    return "\n".join(
        COLUMN_GAP.join(
            _pad(cell, width, left=index == 0) for index, (cell, width) in enumerate(zip(line, widths, strict=True))
        ).rstrip()
        for line in lines
    )


def _pad(cell: str, width: int, *, left: bool) -> str:
    padding = " " * (width - display_width(cell))
    return cell + padding if left else padding + cell
