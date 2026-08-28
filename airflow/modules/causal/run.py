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
from modules.utility import CONNECTION_ID

logger = logging.getLogger(__name__)


def connection() -> Any:
    # 반환 타입은 provider 버전에 따라 psycopg2/psycopg3 래퍼로 갈린다. 어느 쪽이든 PEP 249다.
    return PostgresHook(postgres_conn_id=CONNECTION_ID).get_conn()


class IncompleteReturnsError(RuntimeError):
    """대상 하나라도 실현 등락이 없다. **반쪽짜리 주를 굳히지 않는다**(2026-08-28).

    8/17 주를 T+5 일봉이 들어오기 전에 돌렸더니 대상 열 중 둘만 남고 경로 여섯이 저장됐는데
    태스크는 성공이었다. 이 DAG은 같은 창을 자동으로 다시 보는 실행이 없어서 그 주가 그대로
    굳는다(CLAUDE.md의 "하루 한 번 도는 확정 수집은 하나라도 실패하면 죽인다"와 같은 자리다).

    **휴장은 이 판정에 안 섞인다.** 실현 등락 SQL이 달력이 아니라 거래일을 세고
    `RETURNS_SCAN_DAYS`가 그 여유를 준다. 그래서 등락이 비었다는 것은 아직 그날이 안 왔거나
    수집이 밀렸다는 뜻이고, 둘 다 지금 돌리면 안 되는 상태다.
    """


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
            return _summary(window, outcome=_NOTHING, paths=0, skipped=True)

        targets = candidates.resolve_targets(conn)
        returns = candidates.fetch_returns(conn, targets, window)
        found = candidates.fetch_candidates(conn, targets, window)
        events, channels = candidates.fetch_vocabulary(conn, window)

    missing = [target.code for target in targets if target.code not in returns]
    if missing:
        # **모델을 부르기 전에 죽는다.** 저장 단계에서 버리면 비용만 쓰고 반쪽을 남긴다.
        raise IncompleteReturnsError(
            f"{len(missing)}/{len(targets)} targets have no realised returns for week "
            f"{week_start} (T+5 falls on {window.reaction_end}): {', '.join(sorted(missing))}"
        )

    # **툴은 연결을 쥔다.** 조사 왕복 동안 살아 있어야 하므로 이 블록이 답을 받을 때까지
    # 열려 있다. 저장은 아래에서 연결을 새로 연다 — 트랜잭션을 조사와 섞지 않는다.
    with closing(connection()) as conn:
        paths = _build_paths(
            window=window,
            returns=returns,
            found=found,
            events=events,
            channels=channels,
            targets=targets,
            toolbox=_toolbox(conn, window, targets),
        )

    # 후보 인용률. **후보에 없어서 못 본 것과 있었는데 안 쓴 것은 다른 문제다**(2026-08-28).
    # 앞쪽은 조립 SQL이 고치고 뒤쪽은 프롬프트가 고치는데, 재지 않으면 어느 쪽인지 모른다.
    # 낮게 유지되면 후보를 넓힐 게 아니라 좁혀서 진하게 줘야 한다는 신호다 — 후보 목록이
    # 길어지면 모델이 "하나 고르기"로 기우는 것이 프로토타입 v1에서 관측됐다(설계 §8.2).
    cited = {ref for path in paths for ref in path.evidence_refs}
    logger.info(
        "week %s cited %d of %d candidates in %d paths",
        week_start,
        len(cited),
        len(found.refs),
        len(paths),
    )

    input_hash = domain.input_hash(
        week_start=week_start,
        target_codes=list(returns),
        candidate_refs=list(found.refs),
    )
    with closing(connection()) as conn:
        outcome = store.store_paths(
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
    return _summary(
        window,
        outcome=outcome,
        paths=len(paths),
        skipped=False,
        targets=len(targets),
        documents=len(found.documents),
        candidates=len(found.refs),
        cited=len(cited),
    )


def _toolbox(connection: Any, window: domain.CausalWindow, targets: Any) -> Any:
    """툴박스를 만드는 자리. **LangChain을 여기서 늦게 import한다.**"""
    from modules.causal.toolbox import CausalToolbox

    return CausalToolbox(connection=connection, window=window, targets=targets)


def _build_paths(toolbox: Any = None, **kwargs: Any) -> tuple[Any, ...]:
    """모델을 부르는 자리. **LangChain을 여기서 늦게 import한다.**"""
    from modules.causal.generation import CausalBuilder
    from modules.llm import causal_model

    return CausalBuilder(causal_model(), toolbox).build(**kwargs)


# 저장까지 못 간 실행(재실행 skip, 경로 0건)이 쓰는 값.
_NOTHING = domain.StoreOutcome(stored=0, new_channels=0)


def _summary(
    window: domain.CausalWindow,
    *,
    outcome: domain.StoreOutcome,
    paths: int,
    skipped: bool,
    targets: int = 0,
    documents: int = 0,
    candidates: int = 0,
    cited: int = 0,
) -> dict[str, Any]:
    # XCom 경계다. Airflow가 Pydantic 모델을 어떻게 직렬화하는지에 기대지 않는다.
    return {
        "week_start": window.week_start.isoformat(),
        "week_end": window.week_end.isoformat(),
        "as_of_at": window.as_of_at.isoformat(),
        "targets": targets,
        "documents": documents,
        "paths": paths,
        "candidates": candidates,
        "cited": cited,
        "stored": outcome.stored,
        "new_channels": outcome.new_channels,
        "skipped": skipped,
    }
