"""검증을 마친 경로를 DB에 쓴다.

**LangChain을 import하지 않는다.** 이 모듈이 아는 것은 연결과 SQL뿐이다.

어휘 upsert → 경로 → 단계를 **한 트랜잭션**에 담는다. 경로만 들어가고 단계가 빠진 상태를
남기면 `chain_key`가 가리키는 것이 DB에 없다.

계약은 `docs/analysis/market-causal-graph.md` 3·6절이다.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import date

from modules.causal.domain import MAX_NEW_CHANNELS, CausalWindow, TargetReturns
from modules.causal.generation import VerifiedPath
from modules.db import TransactionalConnection
from modules.sql import read_sql

logger = logging.getLogger(__name__)

EVENT_UPSERT = read_sql("postgres", "market_event", "upsert.sql")
CHANNEL_UPSERT = read_sql("postgres", "market_channel", "upsert.sql")
PATH_INSERT = read_sql("postgres", "market_causal_path", "insert.sql")
STEP_INSERT = read_sql("postgres", "market_causal_step", "insert.sql")


class VocabularyDriftError(RuntimeError):
    """새 경로 이름이 하나도 기존과 안 맞는다. 다시 불러도 같은 결과다.

    정규화가 깨졌다는 뜻이고, 조용히 넘어가면 다음 주에 어휘가 두 배가 된다(설계 §6).
    """


def chain_key(channel_ids: Sequence[int]) -> str:
    """단계의 `channel_id`를 순서대로 이어 자연키에 담을 문자열로 만든다.

    **순서가 뜻이다.** `할인율 → 밸류에이션`과 그 반대는 다른 주장이라 다른 키여야 한다.
    """
    return ">".join(str(channel_id) for channel_id in channel_ids)


def store_paths(
    connection: TransactionalConnection,
    *,
    window: CausalWindow,
    paths: Sequence[VerifiedPath],
    returns: Mapping[str, TargetReturns],
    input_hash: str,
    llm_run_id: int | None,
    require_reuse: bool = False,
) -> int:
    """경로를 저장하고 실제로 들어간 수를 돌려준다.

    `require_reuse`는 어휘가 이미 쌓인 주에만 켠다 — 첫 주는 전부 새로 만드는 것이 정상이라
    그때 켜면 언제나 죽는다.
    """
    new_channels: dict[str, int] = {}
    event_ids: dict[tuple[str, date], int] = {}
    reused_any = False
    stored = 0
    refused = 0

    for path in paths:
        target = returns.get(path.target_code)
        if target is None:
            # 실현 등락이 없으면 저장할 수 없다(설계 §6). 정상 흐름이라 실패로 만들지 않는다.
            continue

        channel_ids: list[int] = []
        over_budget = False
        for choice in path.channels:
            if choice.existing_id:
                reused_any = True
                channel_ids.append(_node_id(choice.existing_id))
                continue
            if choice.new_name in new_channels:
                channel_ids.append(new_channels[choice.new_name])
                continue
            if len(new_channels) >= MAX_NEW_CHANNELS:
                over_budget = True
                break
            channel_id = _upsert_channel(connection, choice.new_name, window.week_start)
            new_channels[choice.new_name] = channel_id
            channel_ids.append(channel_id)
        if over_budget:
            refused += 1
            continue

        event_id = _resolve_event(connection, path, window, event_ids)
        if event_id is None:
            continue

        path_id = _insert_path(
            connection,
            window=window,
            path=path,
            event_id=event_id,
            channel_ids=channel_ids,
            target=target,
            input_hash=input_hash,
            llm_run_id=llm_run_id,
        )
        if path_id is None:
            continue  # 같은 자연키가 이미 있다. 첫 성공본이 불변이다.
        _insert_steps(connection, path_id, channel_ids)
        stored += 1

    if refused:
        # 조용한 절삭을 만들지 않는다. 몇 건이 왜 빠졌는지 로그가 말해야 한다.
        logger.warning(
            "refused %s causal paths: new channel budget %s exhausted",
            refused,
            MAX_NEW_CHANNELS,
        )
    if require_reuse and paths and not reused_any:
        raise VocabularyDriftError(
            f"no path reused an existing channel; {len(new_channels)} new names were proposed"
        )
    connection.commit()
    return stored


def _node_id(existing_id: str) -> int:
    """`c:12` 또는 `e:812`에서 숫자만 꺼낸다."""
    return int(existing_id.split(":", 1)[-1])


def _upsert_channel(connection: TransactionalConnection, name: str, week_start: date) -> int:
    with connection.cursor() as cursor:
        cursor.execute(CHANNEL_UPSERT, (name, week_start))
        row = cursor.fetchone()
    return int(row[0])


def _resolve_event(
    connection: TransactionalConnection,
    path: VerifiedPath,
    window: CausalWindow,
    cache: dict[tuple[str, date], int],
) -> int | None:
    if path.event.existing_id:
        return _node_id(path.event.existing_id)
    occurred_on = _event_date(path, window)
    if occurred_on is None:
        return None
    key = (path.event.new_name, occurred_on)
    if key not in cache:
        with connection.cursor() as cursor:
            cursor.execute(EVENT_UPSERT, (path.event.new_name, occurred_on, window.week_start))
            row = cursor.fetchone()
        cache[key] = int(row[0])
    return cache[key]


def _event_date(path: VerifiedPath, window: CausalWindow) -> date | None:
    """사건 날짜. **분석한 주보다 미래일 수 없다** — 아직 일어나지 않은 일을 인용할 수 없다.

    모델이 날짜를 안 주거나 모양이 틀리면 대상 주의 금요일로 둔다. 그 주에 일어난 것은
    확실하고, 날짜 하나 때문에 경로를 통째로 버리는 것은 과하다.
    """
    if not path.event_date:
        return window.week_end
    try:
        parsed = date.fromisoformat(path.event_date)
    except ValueError:
        logger.warning("dropping unparsable event_date %r", path.event_date)
        return window.week_end
    return min(parsed, window.week_end)


def _insert_path(
    connection: TransactionalConnection,
    *,
    window: CausalWindow,
    path: VerifiedPath,
    event_id: int,
    channel_ids: Sequence[int],
    target: TargetReturns,
    input_hash: str,
    llm_run_id: int | None,
) -> int | None:
    with connection.cursor() as cursor:
        cursor.execute(
            PATH_INSERT,
            (
                window.week_start,
                event_id,
                path.target_kind,
                path.target_code,
                chain_key(channel_ids),
                path.sign,
                path.confidence,
                path.reasoning,
                target.week,
                target.t1,
                target.t5,
                target.unit,
                input_hash,
                llm_run_id,
            ),
        )
        row = cursor.fetchone()
    return int(row[0]) if row else None


def _insert_steps(
    connection: TransactionalConnection,
    path_id: int,
    channel_ids: Sequence[int],
) -> None:
    with connection.cursor() as cursor:
        for position, channel_id in enumerate(channel_ids, start=1):
            cursor.execute(STEP_INSERT, (path_id, position, channel_id))
