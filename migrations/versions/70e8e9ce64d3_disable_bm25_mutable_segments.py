"""disable bm25 mutable segments

Revision ID: 70e8e9ce64d3
Revises: b3f9c72e1d54
Create Date: 2026-09-02 10:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "70e8e9ce64d3"
down_revision: str | Sequence[str] | None = "b3f9c72e1d54"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# BM25 검색이 수 초~수십 초 걸려 멈춘 것처럼 보였다(2026-09-02). 원인은 청킹도 PDF 텍스트
# 크기도 아니고 **pg_search의 쓰기 버퍼(mutable segment)가 색인을 읽기 시점으로 미루는
# 구조**다. 설계는 `docs/analysis/pdf-parsing-bm25.md` 8.2다.
#
# ## 무엇이 느렸나 (pg_search 0.25.2 소스와 운영 실측)
#
# - mutable segment는 새 행의 ctid만 쌓는 버퍼다. INSERT·UPDATE 시점에 형태소 분석을 하지
#   않는다(`postgres/insert.rs`의 `InsertMode::Mutable`).
# - 그 대신 **질의마다** 버퍼의 행을 힙에서 읽고 TOAST를 풀어 `pdb.lindera('korean')`을
#   평가한 뒤 메모리 안에 임시 인덱스를 새로 만든다(`index/directory/mvcc.rs`의
#   `index_memory_segment`). 계획 단계에서도 일어난다.
# - 버퍼는 `mutable_segment_rows`(기본 1000)행이 찰 때까지 얼지 않는다. 첨부 텍스트는
#   평균 20,531자, 최대 307,511자라(975건 실측) 수백 행이 쌓이면 검색 하나가 수백만 자를
#   형태소 분석한다. `document_bm25`는 버퍼에 80행(평균 1.8천 자)만으로도 145ms였고
#   `document_attachment_bm25`는 버퍼가 비어 있을 때 3ms였다.
# - 이 버퍼는 초당 수천 건을 쓰는 워크로드용이다. 이 저장소는 시간당 수십~수백 행을 배치로
#   쓰고 하루 수십 회 읽는다. 버퍼가 주는 것이 없고 읽기만 비싸다.
#
# ## 왜 0인가
#
# 허용 범위는 0~10000이고 0이면 버퍼 없이 바로 immutable segment로 쓴다
# (`InsertMode::Immutable`). 형태소 분석 비용은 쓰는 쪽(시간당 DAG)이 그때 한 번 문다.
#
# ## VACUUM을 함께 돌리는 이유
#
# 이미 쌓인 mutable segment는 옵션을 0으로 바꿔도 저절로 사라지지 않는다. 0이 되면
# mergeable로 판정되지만(`storage/block.rs`의 `is_mergeable`) INSERT 시점의 머지는 그것을
# 집지 않았고, **VACUUM이 백그라운드 머지를 띄워 immutable로 바꿨다**(로컬 ParadeDB 0.25.2
# 실측, 80행이 3초 안에 바뀜). autovacuum을 기다리면 그때까지 `document_bm25`는 질의마다
# 80행을 다시 분석한다. VACUUM은 트랜잭션 안에서 못 돌아 `autocommit_block`으로 감싼다 —
# ALTER 둘은 그 앞에서 커밋된다. `paradedb.force_merge`는 0.25.2에서 deprecated이고
# "run VACUUM instead"를 낸다.
#
# 운영 `document_attachment_bm25`에는 손으로 넣은 `mutable_segment_rows=5`가 있었다
# (2026-09-02 확인, 저장소에는 없음). 5도 동작은 하지만 5행마다 세그먼트가 생기고 mutable
# 세그먼트가 셋을 넘으면 쓰는 쪽이 foreground 머지를 문다(`postgres/merge.rs`의
# `need_backpressure`). 이 리비전이 그 값을 덮는다.
#
# reloption 변경은 인덱스에 AccessExclusiveLock을 잡지만 O(1)이라 시간당 DAG을 막지 않는다.
# REINDEX는 없다 — 세그먼트 변환은 머지가 하고 형태소 분석은 mutable 행에만 다시 든다.
INDEXES = ("document_bm25", "document_attachment_bm25")
TABLES = ("document", "document_attachment")


def upgrade(engine_name: str) -> None:
    _run(f"upgrade_{engine_name}")


def downgrade(engine_name: str) -> None:
    _run(f"downgrade_{engine_name}")


def _run(name: str) -> None:
    # A revision written before an alias existed has no section for it, and
    # there is nothing for that alias to do. Adding an alias must not force a
    # no-op edit to every past revision.
    operations = globals().get(name)
    if operations is not None:
        operations()


def upgrade_default() -> None:
    for index in INDEXES:
        op.execute(f"ALTER INDEX {index} SET (mutable_segment_rows = 0)")
    # 이미 쌓인 mutable segment를 immutable로 바꾼다(위 "VACUUM을 함께 돌리는 이유").
    with op.get_context().autocommit_block():
        for table in TABLES:
            op.execute(f"VACUUM {table}")


def downgrade_default() -> None:
    # 기본값(1000)으로 되돌린다. 값을 다시 적지 않고 RESET이라 기본값이 바뀌어도 따라간다.
    for index in INDEXES:
        op.execute(f"ALTER INDEX {index} RESET (mutable_segment_rows)")
