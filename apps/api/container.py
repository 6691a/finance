"""조회 API의 composition root.

**여기서 선언한 것이 생성자로 주입된다.** 업무 코드는 파라미터로 의존성을 받고,
컨테이너를 들여다보는 자리는 라우터의 `@inject` 경계 하나뿐이다 —
provider를 업무 코드가 `container.x()`로 직접 부르면 그건 Service Locator이지
의존성 주입이 아니다.

**`apps/core/container.py`를 그대로 쓰지 않는다.** 그 모듈은 본문에서
`from apps.core.config import settings`를 해서 import만으로 `config.yaml`을 요구하고,
`default_session_factory`가 별칭을 `default`로 못 박아 뒀다. 이 서비스는 `read_only`
별칭에 붙어야 하므로 둘 다 맞지 않는다. 대신 `settings`와 `db_alias`를
`providers.Dependency()`로 **밖에서 받는다** — 채우는 자리는 `main.py` 하나다.
"""

from dependency_injector import containers, providers
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.core.database import Database


def session_factory_for(database: Database, alias: str) -> async_sessionmaker[AsyncSession]:
    """별칭 하나의 세션 팩토리. `Database`가 별칭별 엔진을 이미 들고 있다."""
    return database.get_session_factory(alias)


class ApiContainer(containers.DeclarativeContainer):
    """`WiringConfiguration`이 `Provide` 마커를 푸는 자리를 지정한다.

    라우터 패키지가 그 자리다 — 거기서만 컨테이너 이름이 보이고, 리포지토리와 서비스는
    자기가 어느 컨테이너에서 왔는지 모른다.

    **모듈이 아니라 패키지로 건다.** 리소스마다 파일이 하나씩 늘기 때문에, 모듈을
    나열하면 새 파일을 더할 때 여기도 함께 고쳐야 하고 빠뜨리면 `Provide` 객체가
    그대로 주입되어 조용히 틀린다.
    """

    wiring_config = containers.WiringConfiguration(packages=["apps.api.routes"])

    # `main.py`가 채운다. 여기서 읽으면 import만으로 config.yaml이 필요해진다.
    settings = providers.Dependency()
    db_alias = providers.Dependency(instance_of=str)

    # 엔진 풀은 프로세스 하나에 한 벌이다. `Singleton`이 그 수명을 뜻한다.
    database: providers.Singleton[Database] = providers.Singleton(
        Database,
        databases=settings.provided.databases,
    )

    # **세션 팩토리를 주입한다.** 세션이 아니라 팩토리다 — 리포지토리가 조회 단위로
    # 열고 닫는다(`apps/realtime/repository.py`가 같은 모양이다).
    session_factory = providers.Callable(
        session_factory_for,
        database=database,
        alias=db_alias,
    )
