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


class EmptyAnswerError(RuntimeError):
    """교정 뒤에도 경로가 0건이다. **그 주를 0행으로 굳히지 않는다**(2026-08-31 조사 G-37).

    전에는 `store_paths(paths=())`가 0행을 쓰고 태스크가 성공해 "그 주에 인과가 없었다"와
    "모델이 두 번 다 못 냈다"가 XCom에서 같은 모양이었다. 후보가 0건인 주도 모델은 실현
    등락만으로 경로를 내므로, 두 번 다 빈 답은 프롬프트나 모델 쪽 문제다.
    """


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
    try_number: int = 1,
) -> dict[str, Any]:
    """한 주를 되짚어 경로를 저장한다. XCom에 실릴 요약을 돌려준다.

    **대화 하나가 원장 행 하나다.** 모델을 부르기 전에 `running`으로 열고, 어떻게 끝나든
    닫는다 — 실패한 대화가 원장에 없으면 "안 돌았다"와 "돌다 죽었다"를 못 가른다(G-37).
    """
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
        toolbox = _toolbox(conn, window, targets)
        builder = _builder(_causal_model(), toolbox)
        llm_run_id = store.start_llm_run(
            conn,
            kind="causal",
            run_date=week_start,
            as_of_at=window.as_of_at,
            dag_run_id=dag_run_id,
            try_number=try_number,
            llm_model=builder.model_name,
            prompt_version=domain.PROMPT_VERSION,
        )
        # 넓게 잡되 **반드시 다시 올린다.** 잡는 이유는 원장을 닫는 것 하나뿐이다.
        try:
            built = _build_paths(
                builder=builder,
                window=window,
                returns=returns,
                found=found,
                events=events,
                channels=channels,
                targets=targets,
                prices=_weekly_closes(toolbox, window, returns),
            )
        except BaseException as error:
            store.finish_llm_run(
                conn,
                llm_run_id,
                status="failed",
                subjects_requested=len(targets),
                subjects_answered=0,
                usage=builder.usage,
                error=f"{type(error).__name__}: {error}",
            )
            raise
        paths, links = built.paths, built.links
        if built.attempts and not paths:
            empty = EmptyAnswerError(
                f"the model returned no causal path for week {week_start} even after one repair"
            )
            store.finish_llm_run(
                conn,
                llm_run_id,
                status="failed",
                subjects_requested=len(targets),
                subjects_answered=0,
                tool_rounds=built.tool_rounds,
                investigation_truncated=built.investigation_truncated,
                usage=built.usage,
                error=str(empty),
            )
            raise empty
        store.finish_llm_run(
            conn,
            llm_run_id,
            status="succeeded",
            subjects_requested=len(targets),
            subjects_answered=len({path.target_code for path in paths}),
            tool_rounds=built.tool_rounds,
            investigation_truncated=built.investigation_truncated,
            usage=built.usage,
        )

    # 후보 인용률. **후보에 없어서 못 본 것과 있었는데 안 쓴 것은 다른 문제다**(2026-08-28).
    # 앞쪽은 조립 SQL이 고치고 뒤쪽은 프롬프트가 고치는데, 재지 않으면 어느 쪽인지 모른다.
    # 낮게 유지되면 후보를 넓힐 게 아니라 좁혀서 진하게 줘야 한다는 신호다 — 후보 목록이
    # 길어지면 모델이 "하나 고르기"로 기우는 것이 프로토타입 v1에서 관측됐다(설계 §8.2).
    cited = {ref for path in paths for ref in path.evidence_refs}
    cited |= {ref for link in links for ref in link.evidence_refs}
    logger.info(
        "week %s cited %d of %d candidates in %d paths (%d linked)",
        week_start,
        len(cited),
        len(found.refs),
        len(paths),
        len(links),
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
            llm_run_id=llm_run_id,
            # 어휘가 비어 있으면 전부 새로 만드는 것이 정상이다. 그때 재사용을 강제하면
            # 첫 주가 언제나 죽는다.
            require_reuse=bool(channels),
            links=links,
        )
    return _summary(
        window,
        outcome=outcome,
        paths=len(paths) + len(links),
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


def _weekly_closes(
    toolbox: Any, window: domain.CausalWindow, returns: Any
) -> dict[str, tuple[domain.DailyClose, ...]]:
    """대상별 그 주 일별 종가. **모델이 아니라 코드가 싣는다**(설계 §11.3).

    툴 호출이 모델 재량이라 링커가 볼 숫자가 대화에 없을 수 있다 — 2026-08-10 주 운영
    실행에서 모델은 `price_window`를 한 번도 부르지 않았다. SQL은 그 툴이 쓰는 넷을 그대로
    쓰고 부르는 쪽만 모델에서 코드로 바뀐다.
    """
    closes: dict[str, tuple[domain.DailyClose, ...]] = {}
    for code in returns:
        closes[code] = tuple(
            domain.DailyClose(business_date=row.business_date, close=row.close)
            for row in toolbox.price_window(code, 1).rows
            if window.week_start <= row.business_date <= window.week_end
        )
    return closes


def _causal_model() -> Any:
    """모델을 만드는 자리. **LangChain을 여기서 늦게 import한다.**"""
    from modules.llm import causal_model

    return causal_model()


def _builder(model: Any, toolbox: Any) -> Any:
    """빌더를 만드는 자리. 원장이 열리기 전에 있어야 모델 이름을 적을 수 있다."""
    from modules.causal.generation import CausalBuilder

    return CausalBuilder(model, toolbox)


def _build_paths(builder: Any, **kwargs: Any) -> domain.BuildResult:
    """모델을 부르는 자리. 테스트가 이 함수를 갈아 끼운다."""
    return builder.build(**kwargs)


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
        "linked": outcome.linked,
        "new_channels": outcome.new_channels,
        "skipped": skipped,
    }
