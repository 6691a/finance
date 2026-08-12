"""장중 수집기가 "오늘 이 시장이 열었나"를 묻는 곳.

**답을 모르면 `None`이다.** 호출자는 `None`과 `True`를 같게 다뤄 시세 수집을 계속한다.
캘린더 수집이 실패했거나 새 연도를 아직 못 채운 상태 때문에 진짜 거래일 데이터를 잃는 것이
휴장일에 빈 요청을 몇 번 더 보내는 것보다 나쁘다.

이 모듈은 Airflow를 import하지 않는다. import하면 수집기 테스트가 배포 환경 없이 돌지 않는다.
"""

from datetime import date
from typing import Any, Protocol, Self

from modules.sql import read_sql

MARKET_SESSION_SELECT = read_sql("postgres", "market_session", "select_open_day.sql")

KRX = "KRX"
US_EQUITY = "US_EQUITY"


class Cursor(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, *args: object) -> bool | None: ...

    def execute(self, statement: str, parameters: Any) -> object: ...

    def fetchone(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


def market_open_day(connection: Connection, market_code: str, session_date: date) -> bool | None:
    """그 시장의 그 날짜 개장 여부. 행이 없거나 아직 판정하지 않았으면 `None`."""
    with connection.cursor() as cursor:
        cursor.execute(MARKET_SESSION_SELECT, (market_code, session_date))
        row = cursor.fetchone()
    if row is None:
        return None
    return row[0]


def krx_open_day(connection: Connection, session_date: date) -> bool | None:
    """국내 거래일 여부. 날짜는 KST 기준이다."""
    return market_open_day(connection, KRX, session_date)


def us_equity_open_day(connection: Connection, session_date: date) -> bool | None:
    """미국 현물장 개장 여부.

    **날짜는 `America/New_York` 기준이다.** 미국 정규장은 KST로 전날 22:30에 시작해 당일
    05:00에 끝나므로, KST 날짜로 물으면 세션의 절반이 엉뚱한 날을 본다.
    """
    return market_open_day(connection, US_EQUITY, session_date)
