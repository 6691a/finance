from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SortDirection(StrEnum):
    """정렬 방향. 조회 메서드가 정렬 키와 함께 받는다."""

    ASC = "asc"
    DESC = "desc"


class InvalidPeriodError(ValueError):
    """조회 기간이 뒤집혔거나 허용 범위를 넘었다.

    호출자가 잘못 부른 것이므로 빈 결과와 구분한다. 빈 결과는 "그 기간에 데이터가 없다"는
    사실이고 이 예외는 "물어본 방식이 틀렸다"는 뜻이다. 재시도해도 같은 결과다.
    """


class BaseRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    def session(self) -> AsyncSession:
        """조회 하나가 쓸 세션.

        `async with repository.session() as session:` 형태로 쓴다. 읽기 전용 조회라
        커밋하지 않으며, 블록을 벗어날 때 세션이 닫히면서 트랜잭션도 함께 끝난다.
        """
        return self._session_factory()
