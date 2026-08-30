"""검증을 마친 경로를 DB에 쓴다.

**LangChain을 import하지 않는다.** 이 모듈이 아는 것은 연결과 SQL뿐이다.

어휘 upsert → 경로 → 단계를 **한 트랜잭션**에 담는다. 경로만 들어가고 단계가 빠진 상태를
남기면 `chain_key`가 가리키는 것이 DB에 없다.

계약은 `docs/analysis/market-causal-graph.md` 3·6절이다.
"""

import logging
from collections.abc import Mapping, Sequence
from datetime import date

from modules.causal import domain
from modules.causal.domain import (
    CausalWindow,
    LinkedPath,
    StoreOutcome,
    TargetReturns,
    VerifiedPath,
)
from modules.db import TransactionalConnection
from modules.sql import read_sql

logger = logging.getLogger(__name__)

EVENT_UPSERT = read_sql("postgres", "market_event", "upsert.sql")
CHANNEL_UPSERT = read_sql("postgres", "market_channel", "upsert.sql")
PATH_INSERT = read_sql("postgres", "market_causal_path", "insert.sql")
STEP_INSERT = read_sql("postgres", "market_causal_step", "insert.sql")
EVIDENCE_INSERT = read_sql("postgres", "market_causal_evidence", "insert.sql")
WEEK_EXISTS = read_sql("postgres", "market_causal_path", "exists_by_week.sql")


class VocabularyDriftError(RuntimeError):
    """새 경로 이름이 하나도 기존과 안 맞는다. 다시 불러도 같은 결과다.

    정규화가 깨졌다는 뜻이고, 조용히 넘어가면 다음 주에 어휘가 두 배가 된다(설계 §6).
    """


def week_has_paths(connection: TransactionalConnection, week_start: date) -> bool:
    """그 주에 경로가 이미 있나. **재실행 판정이 이것이다**(설계 §5.4).

    `input_hash`로 판정하지 않는다 — 그것은 감사 값이라, 후보가 조금 달라진 재실행이 같은
    주에 행을 한 벌 더 만들면 안 된다.
    """
    with connection.cursor() as cursor:
        cursor.execute(WEEK_EXISTS, {"week_start": week_start})
        row = cursor.fetchone()
    return bool(row and row[0])


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
    links: Sequence[LinkedPath] = (),
) -> StoreOutcome:
    """경로를 저장하고 무엇을 했는지 돌려준다.

    `require_reuse`는 어휘가 이미 쌓인 주에만 켠다 — 첫 주는 전부 새로 만드는 것이 정상이라
    그때 켜면 언제나 죽는다.

    **새 이름 수에 상한을 두지 않는다**(2026-08-28 제거). 전에는 상한을 넘긴 경로를 통째로
    버렸는데, 두 번 다 상한이 데이터를 잘랐다 — 개발 첫 주 19개 중 17개, 운영 재실행 20개
    중 4개다. 어휘 목록은 이미 프롬프트에 **전부** 실리므로(`vocabulary_block`) 모델이 보고도
    새 이름을 만들면 그건 진짜 새 채널로 봐야 한다. 8주 프로토타입도 상한 없이 8개로
    수렴했다. 폭주는 아래 `VocabularyDriftError`가 막는다 — 그쪽은 데이터를 버리지 않고
    태스크를 죽인다.
    """
    # 이름 → id. 이 실행이 `_upsert_channel`로 해결한 이름 전부다.
    resolved: dict[str, int] = {}
    # 그중 **이번 주에 처음 생긴 것**. 어휘 수렴을 재는 값은 이쪽이다(2026-08-30 정정).
    #
    # 전에는 `resolved`의 크기를 그대로 썼는데 그것은 "새 이름 수"가 아니라 "이름으로 넘긴
    # 횟수"다. upsert는 이름이 이미 있으면 행을 안 만들고 기존 id를 돌려주므로 둘이 다르다.
    # **링커가 그 차이를 상시화했다** — 링커는 저장 전 채널의 `c:<id>`를 알 수 없어 이름만
    # 낼 수 있고(설계 §11.4), 그래서 기존 채널을 완벽히 재사용해도 전부 새 이름으로 세어졌다.
    # 2026-08-17 주 실측에서 링커가 쓴 셋이 그대로 `new_channels: 3`이 됐는데 실제로 생긴
    # 채널은 0이었다.
    created: set[str] = set()
    event_ids: dict[tuple[str, date], int] = {}
    reused_any = False
    stored = 0
    linked = 0

    def channel_id_for(name: str) -> int:
        if name in resolved:
            return resolved[name]
        channel_id, first_seen = _upsert_channel(connection, name, window.week_start)
        resolved[name] = channel_id
        if first_seen == window.week_start:
            created.add(name)
        return channel_id

    for path in paths:
        target = returns.get(path.target_code)
        if target is None:
            # 실현 등락이 없으면 저장할 수 없다(설계 §6). 정상 흐름이라 실패로 만들지 않는다.
            continue

        channel_ids: list[int] = []
        for choice in path.channels:
            if choice.existing_id:
                reused_any = True
                channel_ids.append(_node_id(choice.existing_id))
                continue
            channel_ids.append(channel_id_for(choice.new_name))

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
        _insert_evidence(connection, path_id, path.evidence_refs)
        stored += 1

    # **대상에서 출발한 경로는 사건 경로 뒤에 저장한다**(설계 §11.4). 링커가 쓴 채널 이름이
    # 첫 답이 방금 만든 것과 같으면 위에서 이미 upsert돼 있어 같은 노드에 붙는다.
    for link in links:
        target = returns.get(link.target_code)
        if target is None:
            continue
        channel_ids = [channel_id_for(name) for name in link.channels]
        path_id = _insert_linked_path(
            connection,
            window=window,
            link=link,
            channel_ids=channel_ids,
            target=target,
            input_hash=input_hash,
            llm_run_id=llm_run_id,
        )
        if path_id is None:
            continue
        _insert_steps(connection, path_id, channel_ids)
        _insert_evidence(connection, path_id, link.evidence_refs)
        linked += 1

    if created:
        # 어휘가 수렴하는지는 이 수가 말한다. 매주 늘기만 하면 정규화가 안 되고 있는 것이다.
        logger.info("causal vocabulary grew by %s channels: %s", len(created),
                    ", ".join(sorted(created)))
    # **판정은 답이 id로 고른 것만 본다.** 링커는 `existing_id`를 낼 수 없어(설계 §11.4)
    # 기존 채널을 완벽히 재사용해도 `reused_any`를 켜지 않는다. 이 가드가 재는 것은
    # "모델이 후보 목록을 보고 골랐나"이고 링커는 그 목록을 받지 않으므로 그것이 맞다 —
    # 링커의 재사용으로 이 가드를 달래면 정작 후보 목록이 안 먹히는 주를 못 잡는다.
    if require_reuse and paths and not reused_any:
        raise VocabularyDriftError(
            f"no path reused an existing channel; {len(created)} new names were created "
            f"from {len(resolved)} proposed"
        )
    connection.commit()
    return StoreOutcome(stored=stored, new_channels=len(created), linked=linked)


def _node_id(existing_id: str) -> int:
    """`c:12` 또는 `e:812`에서 숫자만 꺼낸다."""
    return int(existing_id.split(":", 1)[-1])


def _upsert_channel(
    connection: TransactionalConnection, name: str, week_start: date
) -> tuple[int, date]:
    """채널 id와 그 채널이 **처음 나온 주**를 돌려준다.

    upsert라 이름이 이미 있으면 행이 안 생기고 예전 주가 돌아온다. 부르는 쪽이 그것을
    `week_start`와 비교해 어휘가 실제로 늘었는지 판정한다.
    """
    with connection.cursor() as cursor:
        cursor.execute(CHANNEL_UPSERT, (name, week_start))
        row = cursor.fetchone()
    return int(row[0]), row[1]


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
                domain.event_source_key(event_id),
                None,
                None,
                None,
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


def _insert_linked_path(
    connection: TransactionalConnection,
    *,
    window: CausalWindow,
    link: LinkedPath,
    channel_ids: Sequence[int],
    target: TargetReturns,
    input_hash: str,
    llm_run_id: int | None,
) -> int | None:
    """대상에서 출발한 경로 하나. **`event_id`가 NULL이고 `source_target_*` 셋이 채워진다.**"""
    with connection.cursor() as cursor:
        cursor.execute(
            PATH_INSERT,
            (
                window.week_start,
                None,
                domain.target_source_key(
                    link.source_target_kind, link.source_target_code, link.source_sign
                ),
                link.source_target_kind,
                link.source_target_code,
                link.source_sign,
                link.target_kind,
                link.target_code,
                chain_key(channel_ids),
                link.sign,
                link.confidence,
                link.reasoning,
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


def _insert_evidence(
    connection: TransactionalConnection,
    path_id: int,
    refs: Sequence[str],
) -> None:
    """근거를 남긴다. **비어 있는 것이 정상 흐름이다** — 그 주에 평가된 문서가 하나도
    없으면 프롬프트가 `evidence_refs`를 빈 목록으로 두라고 시킨다."""
    if not refs:
        return
    with connection.cursor() as cursor:
        for ref in refs:
            cursor.execute(EVIDENCE_INSERT, (path_id, ref))
