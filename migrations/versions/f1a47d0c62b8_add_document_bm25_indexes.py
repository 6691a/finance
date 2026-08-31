"""add document and attachment bm25 indexes

Revision ID: f1a47d0c62b8
Revises: e5c93b18ad7f
Create Date: 2026-08-31 21:20:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a47d0c62b8"
down_revision: str | Sequence[str] | None = "e5c93b18ad7f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 문서 검색의 1단계다. 설계는 `docs/analysis/pdf-parsing-bm25.md` 8절이고, 벡터·임베딩은
# 그 문서 12절의 조건이 관측될 때 켠다.
#
# **`tsvector`가 아니라 `pg_search`(BM25)인 이유**는 한국어 형태소다. `tsvector`에는 한국어
# 분석기가 없어 `삼성전자가`와 `삼성전자는`이 따로 잡히는데 lindera는 그것을 가른다.
# 운영·개발 DB가 모두 ParadeDB(`paradedb/paradedb:0.25.2-pg17`)이고 `pg_search`가 이미
# `shared_preload_libraries`에 올라가 있다. 새 인프라가 없다.
#
# **문법은 버전을 탄다.** 아래 형태는 0.25.2에서 실제로 만들고 조회해 확인했다(2026-08-31):
# 다중 컬럼 캐스트가 되고, `body`가 NULL인 행도 `title` 쪽으로 걸린다.
# `pdb.lindera('korean')`은 인덱스 토크나이저 이름이 아니라 **컬럼에 씌우는 캐스트**다.
#
# **모델(`apps/models`)은 이 인덱스를 선언하지 않는다.** 표현식 인덱스라 autogenerate가
# 같은 모양으로 렌더링하지 못해 매번 차이를 만든다. 이 저장소는 운영 DB에 autogenerate를
# 돌리지 않고 리비전을 손으로 쓰므로(`.claude/skills/writing-migrations`) 그 선택이 안전하다.
#
# ## CONCURRENTLY인 이유 (2026-08-31 운영 실측)
#
# 처음에는 평범한 `CREATE INDEX`였다. 운영에서 돌려 보니 **document 3,864행의 본문을 lindera로
# 형태소 분석하는 데 수 분이 걸리고, 그동안 `document`에 ShareLock이 걸려 쓰기가 전부 막혔다.**
# `document_body_hourly`의 UPDATE가 그 뒤에 줄을 섰고, 대기 큐 뒤의 SELECT까지 따라 막혔다.
# 병렬 워커도 안 쓴다 — `paradedb.min_rows_per_worker`가 300,000이라 이 크기의 테이블은
# 단일 코어로 돈다.
#
# 그래서 `CONCURRENTLY`다. 테이블을 두 번 훑어 전체 시간은 더 걸리지만 **쓰기를 막지 않는다.**
# 시간당 DAG이 계속 쓰는 테이블이라 그 교환이 맞다.
#
# **대가가 둘 있다.**
#
# - `CONCURRENTLY`는 트랜잭션 블록 안에서 못 돈다. 그래서 `autocommit_block()`으로 감싼다.
#   이 리비전은 **원자적이지 않다** — 앞 인덱스가 커밋된 뒤 뒤 인덱스가 실패할 수 있다.
# - 실패하면 **INVALID 인덱스가 남는다.** 그때는 손으로 지운다:
#   `SELECT indexrelid::regclass FROM pg_index WHERE NOT indisvalid;` 뒤 `DROP INDEX`.
#   `IF NOT EXISTS`를 붙여 뒀지만 그것은 INVALID 인덱스도 "있다"로 보므로, 다시 돌리기 전에
#   반드시 지운다.
DOCUMENT_INDEX = "document_bm25"
ATTACHMENT_INDEX = "document_attachment_bm25"


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
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_search")
    # CONCURRENTLY는 트랜잭션 블록 안에서 못 돈다. 위 주석의 "대가가 둘" 참고.
    with op.get_context().autocommit_block():
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS {DOCUMENT_INDEX} ON document
            USING bm25 (id, (title::pdb.lindera('korean')), (body::pdb.lindera('korean')))
            WITH (key_field='id')
            """
        )
        op.execute(
            f"""
            CREATE INDEX CONCURRENTLY IF NOT EXISTS {ATTACHMENT_INDEX} ON document_attachment
            USING bm25 (id, (extracted_text::pdb.lindera('korean')))
            WITH (key_field='id')
            """
        )


def downgrade_default() -> None:
    # 지울 때도 쓰기를 막지 않는다. INVALID로 남은 인덱스도 이 문장이 지운다.
    with op.get_context().autocommit_block():
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {ATTACHMENT_INDEX}")
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {DOCUMENT_INDEX}")
    # 확장은 지우지 않는다. 다른 것이 쓰고 있을 수 있고, 되돌리기의 목적은 인덱스 제거다.
