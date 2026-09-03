"""add kospi forecast

Revision ID: a1c74f0b8e35
Revises: 70e8e9ce64d3
Create Date: 2026-09-02 21:00:00.000000

코스피 일일 전망의 표 셋. 설계는 `docs/analysis/kospi-forecast.md`에 있다.

- `kospi_forecast` — 자연키 `(run_date, slot)`이고 `ON CONFLICT DO NOTHING`이다.
  **첫 성공본 불변** — 채점 칸 넷만 나중에 한 번 채워진다.
- `kospi_llm_run` — 모델 호출 하나가 행 하나다. 모델을 부르기 전에 `running`으로 열고
  어떻게 끝나든 닫는다.
- `kospi_tool_call` — 그 대화 안의 툴 호출 하나. 검증 전·후 인자와 결과 전문을 남긴다.

**관계와 메모 테이블이 없다.** 그쪽 원본은 Neo4j다(사용자 결정, 2026-09-02) — 여기 만들면
원본이 둘이 되고 어느 쪽이 맞는지 정해야 한다.

**옛 `thesis*`·`market_causal_*`을 여기서 지우지 않는다.** 삭제는 새 DAG 셋이 운영에서
돈 뒤 별도 리비전이고, 그 전에 운영에 무엇이 들어 있는지 읽기 전용으로 본다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
검증은 오프라인 `head_sql` 기반 `tests/migrations/`가 한다.

모델(`apps/models/analysis/kospi.py`)과 여기의 CHECK 문자열·컬럼 주석은 **글자 그대로**
같아야 한다. 다르면 다음 autogenerate가 매번 차이를 만든다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a1c74f0b8e35"
down_revision: str | Sequence[str] | None = "70e8e9ce64d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(*values: str) -> sa.Enum:
    """모델의 `_enum_column`과 같은 형태. native enum을 쓰지 않는다(프로젝트 규칙)."""
    return sa.Enum(*values, native_enum=False, length=20)


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
    op.create_table(
        "kospi_llm_run",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="대리키"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 생성 시각(UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 수정 시각(UTC)",
        ),
        sa.Column(
            "kind",
            _enum("forecast", "review"),
            nullable=False,
            comment="대화의 종류(forecast는 전망, review는 장후 관찰). 프롬프트도 판도 다르다",
        ),
        sa.Column("run_date", sa.Date(), nullable=False, comment="이 대화가 대상으로 삼은 세션 날짜(KST)"),
        sa.Column(
            "slot",
            _enum("pre_open", "midday", "pre_close"),
            nullable=True,
            comment="전망 대화의 슬롯. 장후 관찰은 슬롯이 없어 NULL이다",
        ),
        sa.Column(
            "as_of_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="이 대화의 조회 기준 시각(UTC). 슬롯이 정한다",
        ),
        sa.Column(
            "status",
            _enum("running", "succeeded", "failed"),
            nullable=False,
            comment="대화의 상태. running으로 열고 succeeded 또는 failed로 닫는다",
        ),
        sa.Column("llm_model", sa.Text(), nullable=False, comment="부른 모델 이름"),
        sa.Column(
            "prompt_version",
            sa.Text(),
            nullable=False,
            comment="이 대화가 쓴 프롬프트 판. 판을 안 올리고 문장을 고치면 이 원장이 거짓말을 한다",
        ),
        sa.Column("dag_run_id", sa.Text(), nullable=False, comment="이 대화를 연 Airflow dag_run_id"),
        sa.Column(
            "try_number",
            sa.Integer(),
            nullable=False,
            comment="그 태스크의 시도 번호. 재시도가 대화를 몇 번 열었는지 본다",
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False, comment="대화를 연 시각(UTC)"),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="대화를 닫은 시각(UTC). NULL이면 닫지 못하고 죽은 것이다",
        ),
        sa.Column("error", sa.Text(), nullable=True, comment="실패 사유. 종류와 메시지를 함께 남긴다"),
        sa.Column(
            "tool_rounds",
            sa.Integer(),
            nullable=True,
            comment="조사 왕복 수. 상한에 붙어 있으면 조사가 잘리고 있다는 신호다",
        ),
        sa.Column(
            "tool_calls",
            sa.Integer(),
            nullable=True,
            comment="기록된 툴 호출 수. 모르는 툴과 인자 검증 실패도 세므로 예산 카운터와 다른 수다",
        ),
        sa.Column(
            "tool_result_chars",
            sa.Integer(),
            nullable=True,
            comment="모델에게 실제로 돌아간 툴 결과의 문자 수. 버려진 결과는 안 센다",
        ),
        sa.Column(
            "truncated",
            sa.Boolean(),
            nullable=True,
            comment="조사가 왕복 상한에서 끊겼나. 스스로 끝낸 실행과 잘린 실행을 가르는 유일한 칸이다",
        ),
        sa.Column(
            "rejected",
            sa.Integer(),
            nullable=True,
            comment="검증이 버린 이유·관찰의 수. 남은 수의 분모라 유효율이 여기서 읽힌다",
        ),
        sa.Column(
            "memories_written",
            sa.Integer(),
            nullable=True,
            comment="이 관찰이 새로 쓴 메모 수. 전망 대화는 NULL",
        ),
        sa.Column(
            "memories_rejected",
            sa.Integer(),
            nullable=True,
            comment="중복 또는 상한으로 쓰지 않은 메모 수",
        ),
        sa.Column(
            "memories_kept",
            sa.Integer(),
            nullable=True,
            comment="모델이 keep으로 판정해 유지한 메모 수",
        ),
        sa.Column(
            "memories_dropped",
            sa.Integer(),
            nullable=True,
            comment="모델이 drop으로 판정해 내린 메모 수",
        ),
        sa.Column(
            "memories_unreviewed",
            sa.Integer(),
            nullable=True,
            comment="답에서 빠진 메모 수. keep으로 치지 않는다 — 두 번 연속이면 코드가 내린다",
        ),
        sa.Column(
            "memories_expired",
            sa.Integer(),
            nullable=True,
            comment="나이 상한으로 내린 메모 수. 모델 판정과 무관하게 코드가 정한다",
        ),
        sa.Column(
            "prompt_tokens",
            sa.Integer(),
            nullable=True,
            comment="입력 토큰. 왕복마다 대화 전체가 재전송되므로 프롬프트 크기와 왕복 수가 여기 실린다",
        ),
        sa.Column(
            "cached_tokens",
            sa.Integer(),
            nullable=True,
            comment="입력 중 캐시에서 읽은 토큰. 제공처가 안 알려 주면 0이다. 최적화 효과를 재는 칸이다",
        ),
        sa.Column(
            "completion_tokens",
            sa.Integer(),
            nullable=True,
            comment="출력 토큰. reasoning을 포함한다",
        ),
        sa.Column(
            "reasoning_tokens",
            sa.Integer(),
            nullable=True,
            comment="출력 중 사고 토큰. 대화에 남지 않아 재전송되지도 캐시되지도 않는다",
        ),
        sa.CheckConstraint("kind IN ('forecast', 'review')", name="ck_kospi_llm_run_kind"),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_kospi_llm_run_status",
        ),
        sa.CheckConstraint(
            "slot IS NULL OR slot IN ('pre_open', 'midday', 'pre_close')",
            name="ck_kospi_llm_run_slot",
        ),
        sa.CheckConstraint(
            "(kind = 'review' AND slot IS NULL) OR (kind = 'forecast' AND slot IS NOT NULL)",
            name="ck_kospi_llm_run_slot_shape",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="코스피 전망·관찰의 모델 호출 원장. 실패한 대화도 남는다",
    )
    # 원장을 날짜로 훑는 조회가 손잡이 판단의 기본이다(어느 날 무엇이 잘렸나).
    op.create_index("ix_kospi_llm_run_run_date", "kospi_llm_run", ["run_date", "kind"])

    op.create_table(
        "kospi_forecast",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="대리키"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 생성 시각(UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 수정 시각(UTC)",
        ),
        sa.Column(
            "run_date",
            sa.Date(),
            nullable=False,
            comment="전망이 대상으로 삼은 세션 날짜(KST). 시각은 담지 않는다",
        ),
        sa.Column(
            "slot",
            _enum("pre_open", "midday", "pre_close"),
            nullable=False,
            comment=(
                "전망을 만든 슬롯(pre_open은 장전 08:35, midday는 장중 11:35, pre_close는 마감전 14:35 KST). "
                "슬롯이 기준가의 뜻을 정한다 — 장전은 전일 종가 대비, 장중 둘은 그 시각 현재가 대비다"
            ),
        ),
        sa.Column(
            "as_of_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment=(
                "관측 상태와 툴 조회의 기준 시각(UTC). 벽시계가 아니라 슬롯이 정한다. "
                "event-time cutoff라 이 시각 이후 감지·평가·갱신된 행은 조회에서 뺀다"
            ),
        ),
        sa.Column(
            "base_price",
            sa.Numeric(precision=18, scale=4),
            nullable=False,
            comment="등락률의 분모. 장전은 직전 거래일 확정 종가, 장중은 그 시각 최신 분봉 종가다",
        ),
        sa.Column(
            "base_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="기준가가 만들어진 시각(UTC). 장전은 직전 거래일 15:30 KST, 장중은 그 분봉의 시각이다",
        ),
        sa.Column(
            "so_far_pct",
            sa.Numeric(precision=8, scale=2),
            nullable=True,
            comment=(
                "장중 슬롯만. 전일 종가 대비 현재가 등락률(퍼센트) — '지금까지 얼마나 왔나'다. "
                "예측 축(기준가 대비 마감까지)과 다른 값이라 칸을 나눈다. 장전은 NULL"
            ),
        ),
        sa.Column(
            "direction",
            _enum("up", "down"),
            nullable=False,
            comment="전망 방향(up/down). flat이 없다 — 크기와 폭이 '얼마나 움직이나'를 이미 말한다",
        ),
        sa.Column(
            "expected_change_pct",
            sa.Numeric(precision=8, scale=2),
            nullable=False,
            comment="기준가 대비 기대 등락률(퍼센트). 부호가 있고 direction과 맞는다. 모델이 낸 값을 보정하지 않는다",
        ),
        sa.Column(
            "band_pct",
            sa.Numeric(precision=8, scale=2),
            nullable=False,
            comment="기대 등락률의 ± 폭(퍼센트포인트). 상한이 아니라 폭이라 구간은 기대 ± 이 값이다",
        ),
        sa.Column(
            "reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "검증을 통과한 이유 목록. 항목은 {direction, statement, factor, memory_id, slot_ref}이고 "
                "**순서가 곧 중요도다**(프롬프트가 결론에 크게 작용한 것부터 요구한다). 개수 상한은 없다"
            ),
        ),
        sa.Column(
            "weak",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment="검증을 통과한 이유가 0건인 답. Slack에 머리표가 붙는다 — 정상 답과 같아 보이면 안 된다",
        ),
        sa.Column(
            "rejected_reasons",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="검증이 버린 이유 수. 남은 수(reasons 길이)의 분모라 근거 유효율이 여기서 읽힌다",
        ),
        sa.Column(
            "input_state",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment=(
                "모델이 본 관측 상태 전부(일봉·관계 표·메모·앞 슬롯). 관계와 메모는 Neo4j가 원본이라 "
                "다음 날 바뀐다 — 이 칸이 없으면 '그 전망이 무엇을 보고 나왔나'를 되짚을 수 없다"
            ),
        ),
        sa.Column(
            "actual_change_pct",
            sa.Numeric(precision=8, scale=2),
            nullable=True,
            comment="장후 채점. 기준가 대비 오늘 KRX 확정 종가의 등락률(퍼센트). 종가가 없으면 NULL로 둔다",
        ),
        sa.Column(
            "hit",
            sa.Boolean(),
            nullable=True,
            comment="방향 적중 여부. 실현 등락이 정확히 0이면 false다 — 어느 방향도 아니면 맞은 것이 아니다",
        ),
        sa.Column(
            "within_band",
            sa.Boolean(),
            nullable=True,
            comment="크기 적중 여부. abs(실제 - 기대) <= 폭. 방향이 틀려도 잰다 — 둘은 다른 축이다",
        ),
        sa.Column(
            "graded_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="채점 시각(UTC). NULL이면 아직 안 했다는 뜻이고, 값이 있으면 다시 채점하지 않는다",
        ),
        sa.Column(
            "prompt_version",
            sa.Text(),
            nullable=False,
            comment="이 전망을 만든 프롬프트 판. 채점을 판별로 가르는 축이다",
        ),
        sa.Column(
            "llm_model",
            sa.Text(),
            nullable=False,
            comment="이 전망을 만든 모델 이름. 판과 함께 성적을 가르는 축이다",
        ),
        sa.Column(
            "dag_run_id",
            sa.Text(),
            nullable=False,
            comment="이 행을 쓴 Airflow dag_run_id. 같은 실행의 재시도인지를 DB가 증명한다",
        ),
        sa.Column(
            "llm_run_id",
            sa.BigInteger(),
            nullable=True,
            comment="이 전망을 만든 대화. 원장이 지워져도 전망 행은 남는다",
        ),
        sa.CheckConstraint("slot IN ('pre_open', 'midday', 'pre_close')", name="ck_kospi_forecast_slot"),
        sa.CheckConstraint("direction IN ('up', 'down')", name="ck_kospi_forecast_direction"),
        sa.CheckConstraint(
            "expected_change_pct BETWEEN -10 AND 10", name="ck_kospi_forecast_change_range"
        ),
        sa.CheckConstraint("band_pct BETWEEN 0.1 AND 5", name="ck_kospi_forecast_band_range"),
        sa.CheckConstraint("base_price > 0", name="ck_kospi_forecast_base_price_positive"),
        sa.CheckConstraint("rejected_reasons >= 0", name="ck_kospi_forecast_rejected_reasons"),
        sa.CheckConstraint(
            "(graded_at IS NULL AND actual_change_pct IS NULL AND hit IS NULL AND within_band IS NULL)"
            " OR (graded_at IS NOT NULL AND actual_change_pct IS NOT NULL"
            " AND hit IS NOT NULL AND within_band IS NOT NULL)",
            name="ck_kospi_forecast_grade_all_or_none",
        ),
        sa.CheckConstraint(
            "(slot = 'pre_open' AND so_far_pct IS NULL) OR slot <> 'pre_open'",
            name="ck_kospi_forecast_so_far_shape",
        ),
        sa.ForeignKeyConstraint(["llm_run_id"], ["kospi_llm_run.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_date", "slot", name="uq_kospi_forecast_natural_key"),
        comment="슬롯마다 만든 코스피 일일 전망을 불변으로 보존하는 테이블. 관계와 메모는 Neo4j가 갖는다",
    )
    # 미채점 회수가 이 인덱스를 쓴다. 날짜 상한이 없어 전체를 훑을 수 있는 조회다.
    op.create_index(
        "ix_kospi_forecast_pending",
        "kospi_forecast",
        ["run_date"],
        postgresql_where=sa.text("graded_at IS NULL"),
    )

    op.create_table(
        "kospi_tool_call",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="대리키"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 생성 시각(UTC)",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="레코드 수정 시각(UTC)",
        ),
        sa.Column("llm_run_id", sa.BigInteger(), nullable=False, comment="이 호출이 속한 대화"),
        sa.Column(
            "seq", sa.Integer(), nullable=False, comment="그 대화 안의 순번. 1부터 빈 곳 없이 채워진다"
        ),
        sa.Column(
            "round_no",
            sa.Integer(),
            nullable=False,
            comment="몇 번째 조사 왕복이었나. 한 왕복에 여러 호출이 묶인다",
        ),
        sa.Column(
            "tool_call_id",
            sa.Text(),
            nullable=False,
            comment="제공처가 준 호출 id. 요청과 결과를 잇는 값이다",
        ),
        sa.Column(
            "tool_name", sa.Text(), nullable=False, comment="모델이 부른 툴 이름. 모르는 이름일 수 있다"
        ),
        sa.Column(
            "arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="모델이 보낸 인자 그대로. 검증 전이라 스키마와 어긋날 수 있다",
        ),
        sa.Column(
            "validated_arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment="검증을 통과해 함수에 들어간 인자. NULL이면 함수에 닿지 못했다는 뜻이다",
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="모델이 이 호출을 요청한 시각(UTC)",
        ),
        sa.Column(
            "duration_ms",
            sa.Integer(),
            nullable=True,
            comment="함수 실행에 걸린 시간(밀리초). 함수에 못 닿았으면 NULL",
        ),
        sa.Column(
            "result",
            sa.Text(),
            nullable=True,
            comment="툴이 돌려준 본문 전문. 모델이 실제로 본 스냅샷이다",
        ),
        sa.Column(
            "result_chars",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
            comment="그 본문의 문자 수. 예산 분석의 재료다",
        ),
        sa.Column(
            "error_kind",
            _enum("unknown_tool", "validation", "limit", "execution", "cancelled"),
            nullable=True,
            comment=(
                "실패 종류(unknown_tool·validation은 함수에 못 닿음, limit은 우리 상한, "
                "execution은 실행 중 예외, cancelled는 sibling 실패로 시작조차 못 함)"
            ),
        ),
        sa.Column("error", sa.Text(), nullable=True, comment="모델에게 돌아간 오류 문자열"),
        sa.Column(
            "delivered",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
            comment=(
                "결과가 모델 대화에 실제로 돌아갔나. sibling 실패로 버려진 결과는 오류가 아니라 "
                "'모델만 못 봤다'이고, 인용 분석이 그 구분 위에 선다"
            ),
        ),
        sa.CheckConstraint(
            "error_kind IS NULL OR error_kind IN"
            " ('unknown_tool', 'validation', 'limit', 'execution', 'cancelled')",
            name="ck_kospi_tool_call_error_kind",
        ),
        sa.CheckConstraint(
            "result IS NOT NULL OR error IS NOT NULL",
            name="ck_kospi_tool_call_outcome",
        ),
        sa.ForeignKeyConstraint(["llm_run_id"], ["kospi_llm_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("llm_run_id", "seq", name="uq_kospi_tool_call_natural_key"),
        comment="코스피 전망·관찰 대화 안의 툴 호출 하나. 인자와 결과 전문을 남긴다",
    )


def downgrade_default() -> None:
    op.drop_table("kospi_tool_call")
    op.drop_index("ix_kospi_forecast_pending", table_name="kospi_forecast")
    op.drop_table("kospi_forecast")
    op.drop_index("ix_kospi_llm_run_run_date", table_name="kospi_llm_run")
    op.drop_table("kospi_llm_run")
