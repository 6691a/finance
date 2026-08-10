"""Windows에서도 DAG 테스트를 수집할 수 있게 만드는 shim.

Airflow SDK는 POSIX 전용이다. `airflow.sdk.io.fs`가 모듈 수준에서
`os.register_at_fork`를 부르는데 Windows에는 그 함수가 없다. 그래서
`from airflow.sdk import dag`가 ImportError로 죽고, `tests/dags`의 수집 실패가
pytest 전체 실행을 중단시킨다.

fork 자체가 없는 플랫폼이라 콜백을 기억하지 않는 no-op이 의미상 맞다.

**표준 라이브러리를 먼저 import한다.** `concurrent.futures.thread`, `logging`,
`random` 등은 `hasattr(os, "register_at_fork")`로 fork 지원을 판별한다. shim을 먼저
깔면 이들이 없는 브랜치로 들어가 `'_thread.lock' object has no attribute
'_at_fork_reinit'`로 깨진다. shim보다 먼저 로드해 실제 Windows 동작을 굳혀 둔다.

DAG 테스트를 컨테이너 안에서만 돌린다면 이 파일은 지워도 된다.
"""

import asyncio.events  # noqa: F401  shim보다 먼저 로드해야 한다
import concurrent.futures.thread  # noqa: F401  shim보다 먼저 로드해야 한다
import logging  # noqa: F401  shim보다 먼저 로드해야 한다
import multiprocessing.resource_tracker  # noqa: F401  shim보다 먼저 로드해야 한다
import os
import random  # noqa: F401  shim보다 먼저 로드해야 한다
import threading  # noqa: F401  shim보다 먼저 로드해야 한다
from collections.abc import Callable


def _register_at_fork(
    *,
    before: Callable[[], object] | None = None,
    after_in_parent: Callable[[], object] | None = None,
    after_in_child: Callable[[], object] | None = None,
) -> None:
    """fork가 없는 플랫폼에서의 `os.register_at_fork`. 콜백은 영원히 불리지 않는다."""


if not hasattr(os, "register_at_fork"):
    os.register_at_fork = _register_at_fork  # type: ignore[attr-defined]
