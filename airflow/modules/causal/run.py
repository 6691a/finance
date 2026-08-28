"""주간 인과 그래프 한 실행. DAG이 부르는 유일한 자리다.

**여기가 연결을 열고 순서를 엮는다.** `dags/`에는 스케줄·재시도·실패 분류만 두는 것이
저장소 규칙이라, 흐름은 이 모듈이 갖는다.

**LangChain은 함수 안에서 늦게 import한다.** 이 모듈은 DAG 파일이 모듈 수준에서 끌고 오므로
LangChain이 여기 딸려 오면 DagBag이 그 무게를 문다.

계약은 `docs/analysis/market-causal-graph.md` 2·5·6절이다.
"""

import logging
from contextlib import closing
from datetime import datetime
from typing import Any

from airflow.providers.postgres.hooks.postgres import PostgresHook

from modules.causal import candidates, domain, store

logger = logging.getLogger(__name__)

CONNECTION_ID = "finance_db"


def connection() -> Any:
    # 반환 타입은 provider 버전에 따라 psycopg2/psycopg3 래퍼로 갈린다. 어느 쪽이든 PEP 249다.
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


def build_weekly_graph(
    *,
    logical_date: datetime,
    week_start_param: str | None,
    dag_run_id: str = "",
) -> dict[str, Any]:
    """한 주를 되짚어 경로를 저장한다. XCom에 실릴 요약을 돌려준다."""
    week_start = domain.resolve_week(logical_date, week_start_param)
    window = domain.window_for(week_start)

    with closing(connection()) as conn:
        if store.week_has_paths(conn, week_start):
            # 첫 성공본이 불변이다. **skip이 아니라 성공이다** — 재실행이 정상 흐름이라
            # 매번 노란 태스크를 만들 이유가 없다(설계 §10.4).
            logger.info("week %s already has causal paths; skipping the model", week_start)
            return _summary(window, stored=0, paths=0, skipped=True)

        targets = candidates.resolve_targets(conn)
        returns = candidates.fetch_returns(conn, targets, window)
        found = candidates.fetch_candidates(conn, targets, window)
        events, channels = candidates.fetch_vocabulary(conn, window)

    if not returns:
        # 실현 등락이 하나도 없으면 저장할 수 있는 경로가 없다. 모델을 부를 이유도 없다.
        logger.warning("week %s has no target with realised returns", week_start)
        return _summary(window, stored=0, paths=0, skipped=False)

    paths = _build_paths(
        window=window,
        returns=returns,
        found=found,
        events=events,
        channels=channels,
        targets=targets,
    )

    input_hash = domain.input_hash(
        week_start=week_start,
        target_codes=list(returns),
        candidate_refs=list(found.refs),
    )
    with closing(connection()) as conn:
        stored = store.store_paths(
            conn,
            window=window,
            paths=paths,
            returns=returns,
            input_hash=input_hash,
            llm_run_id=None,
            # 어휘가 비어 있으면 전부 새로 만드는 것이 정상이다. 그때 재사용을 강제하면
            # 첫 주가 언제나 죽는다.
            require_reuse=bool(channels),
        )
    return _summary(window, stored=stored, paths=len(paths), skipped=False)


def _build_paths(**kwargs: Any) -> tuple[Any, ...]:
    """모델을 부르는 자리. **LangChain을 여기서 늦게 import한다.**"""
    from modules.causal.generation import CausalBuilder
    from modules.llm import causal_model

    return CausalBuilder(causal_model()).build(**kwargs)


def _summary(
    window: domain.CausalWindow, *, stored: int, paths: int, skipped: bool
) -> dict[str, Any]:
    # XCom 경계다. Airflow가 Pydantic 모델을 어떻게 직렬화하는지에 기대지 않는다.
    return {
        "week_start": window.week_start.isoformat(),
        "week_end": window.week_end.isoformat(),
        "as_of_at": window.as_of_at.isoformat(),
        "paths": paths,
        "stored": stored,
        "skipped": skipped,
    }
