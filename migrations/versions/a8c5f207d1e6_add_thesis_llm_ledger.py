"""add thesis llm ledger

Revision ID: a8c5f207d1e6
Revises: f4b19c6ea283
Create Date: 2026-08-26 23:30:00.000000

LLM 대화와 그 안의 툴 호출을 남기는 원장 둘. 설계는
`docs/analysis/market-thesis/13-llm-ledger.md`에 있다.

지금 남는 것은 프롬프트에 넣은 관측 상태와 모델이 **인용한** 근거뿐이다. 어떤 툴을 어떤
인자로 몇 번 불렀고 무엇이 돌아왔는지, 무엇을 보고도 인용하지 않았는지는 실행이 끝나면
사라진다. 툴 14개 중 레지스트리에 들어가는 것은 다섯이고 나머지 아홉은 결과가 증발한다.

- `thesis_llm_run` — 대화 한 번. **자연키가 없다.** 실패한 대화도 남겨야 하고 재시도는
  새 대화라 같은 (kind, run_date, run_slot, horizon_days)에 행이 여럿일 수 있다.
- `thesis_tool_call` — 그 대화 안의 호출 하나. 결과 본문을 전문으로 남긴다.
- `thesis.llm_run_id`·`thesis_outcome.narration_run_id` — 원장만 있고 FK가 없으면
  "이 판단이 무엇을 보고 나왔나"를 시각으로 추정해야 하고, 재시도가 있는 순간 틀린다.
  `ON DELETE SET NULL`인 것은 원장이 판단을 인질로 잡으면 안 되기 때문이다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
검증은 오프라인 `head_sql` 기반 `tests/migrations/test_thesis_schema.py`가 한다.

모델(`apps/models/analysis/thesis.py`)과 여기의 CHECK 문자열·컬럼 주석은 **글자 그대로**
같아야 한다. 다르면 다음 autogenerate가 매번 차이를 만든다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "a8c5f207d1e6"
down_revision: str | Sequence[str] | None = "f4b19c6ea283"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LLM_RUN_ID_COMMENT = "이 추론을 만든 LLM 대화. 툴 호출·결과와 정확도를 조인하는 유일한 칸이다"
NARRATION_RUN_ID_COMMENT = "이 해설을 만든 LLM 대화. 채점은 순수 함수라 이 칸이 없다"


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
        "thesis_llm_run",
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
            _enum("forecast", "review", "nxt_review", "narration"),
            nullable=False,
            comment=(
                "대화의 종류(forecast·review·nxt_review는 추론 생성, narration은 사후 해설). "
                "슬롯에서 유도할 수 없다 — 같은 post_close 슬롯에 생성과 해설이 둘 다 있다"
            ),
        ),
        sa.Column(
            "run_date",
            sa.Date(),
            nullable=False,
            comment=(
                "대화가 대상으로 삼은 세션 날짜(KST). **해설이면 원 추론일이다** — "
                "thesis와 같은 축으로 조인하기 위해서다. 실행일은 as_of_at과 dag_run_id가 말한다"
            ),
        ),
        sa.Column(
            "run_slot",
            _enum(
                "pre_open",
                "intraday_morning",
                "intraday_midday",
                "intraday_afternoon",
                "pre_close",
                "post_close",
                "post_nxt_close",
            ),
            nullable=False,
            comment="대상 슬롯. 해설이면 원 추론의 슬롯이다",
        ),
        sa.Column(
            "horizon_days",
            sa.Integer(),
            nullable=True,
            comment=(
                "해설 대화의 지평(1·3·5). 생성 대화는 NULL이다. 해설이 지평마다 갈리는 것은 "
                "툴 조회의 기준 시각이 지평마다 달라 한 대화에 섞을 수 없어서다"
            ),
        ),
        sa.Column(
            "as_of_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="이 대화의 툴 조회 기준 시각(UTC). event-time cutoff다",
        ),
        sa.Column("dag_run_id", sa.Text(), nullable=False, comment="이 대화를 돌린 Airflow dag_run_id"),
        sa.Column(
            "try_number",
            sa.Integer(),
            nullable=False,
            comment=(
                "그 태스크의 시도 번호(1부터). dag_run_id는 재시도에도 같아서 이 칸이 없으면 "
                "재시도 대화를 서로 구분할 방법이 없다"
            ),
        ),
        sa.Column("llm_model", sa.Text(), nullable=False, comment="이 대화를 돈 모델 식별자"),
        sa.Column(
            "prompt_version",
            sa.Text(),
            nullable=False,
            comment="프롬프트 판. 해설은 `<판>/<변형>` 형태라 생성 대화의 판 번호와 체계가 다르다",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="대화를 시작한 시각(UTC). 이 행은 그래프 호출 전에 먼저 커밋된다",
        ),
        sa.Column(
            "finished_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="대화가 끝난 시각(UTC). NULL이면 종료를 기록하지 못했다는 뜻이다",
        ),
        sa.Column(
            "status",
            _enum("running", "succeeded", "failed"),
            nullable=False,
            comment="running·succeeded·failed. running으로 남은 행은 삭제할 찌꺼기가 아니라 감사 기록이다",
        ),
        sa.Column("error", sa.Text(), nullable=True, comment="실패 사유. status가 failed일 때만 채운다"),
        sa.Column(
            "tool_rounds",
            sa.Integer(),
            nullable=False,
            comment="조사 왕복 수. 왕복 하나가 모델 호출 하나다",
        ),
        sa.Column(
            "tool_calls",
            sa.Integer(),
            nullable=False,
            comment=(
                "기록된 툴 호출 수. **상한을 재는 카운터와 다른 수다** — 이 값은 unknown tool과 "
                "인자 검증 실패도 세지만 툴박스의 예산 카운터는 함수에 진입한 것만 센다"
            ),
        ),
        sa.Column(
            "tool_result_chars",
            sa.Integer(),
            nullable=False,
            comment=(
                "모델에게 실제로 돌아간 결과의 누적 문자 수(delivered=true만). **예산 카운터와 "
                "다른 수다** — 그쪽은 버려진 결과도 센다. MAX_TOOL_RESULT_CHARS와 직접 비교하지 않는다"
            ),
        ),
        sa.CheckConstraint(
            "kind IN ('forecast', 'review', 'nxt_review', 'narration')",
            name="ck_thesis_llm_run_kind",
        ),
        sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="ck_thesis_llm_run_status"),
        sa.CheckConstraint(
            "horizon_days IS NULL OR horizon_days IN (1, 3, 5)",
            name="ck_thesis_llm_run_horizon_days",
        ),
        sa.CheckConstraint(
            "(status = 'running' AND finished_at IS NULL AND error IS NULL)"
            " OR (status = 'succeeded' AND finished_at IS NOT NULL AND error IS NULL)"
            " OR (status = 'failed' AND finished_at IS NOT NULL AND error IS NOT NULL)",
            name="ck_thesis_llm_run_status_shape",
        ),
        sa.PrimaryKeyConstraint("id"),
        comment="LLM 대화 한 번의 원장. 툴 호출 패턴과 정확도의 상관을 재려고 남긴다",
    )

    op.create_table(
        "thesis_tool_call",
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
            "seq",
            sa.Integer(),
            nullable=False,
            comment="대화 안의 기록 순서(1부터). **인과 순서가 아니다** — 같은 라운드의 호출은 병렬일 수 있다",
        ),
        sa.Column(
            "round_no",
            sa.Integer(),
            nullable=False,
            comment="몇 번째 tool round의 요청인가(1부터). 한 라운드가 모델 응답 하나다",
        ),
        sa.Column(
            "tool_call_id",
            sa.Text(),
            nullable=False,
            comment="제공처가 준 tool call id. AIMessage의 요청과 ToolMessage의 결과를 잇는 키다",
        ),
        sa.Column("tool_name", sa.Text(), nullable=False, comment="부른 툴 이름"),
        sa.Column(
            "arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="모델이 보낸 인자 원본(StructuredTool 검증 전). AIMessage.tool_calls의 args 그대로다",
        ),
        sa.Column(
            "validated_arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
            comment=(
                "검증·기본값 적용 뒤 실제 함수에 들어간 인자. unknown tool과 인자 검증 실패는 "
                "함수에 진입하지 않아 NULL이다"
            ),
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            comment="모델의 호출 요청을 등록한 시각(UTC)",
        ),
        sa.Column(
            "duration_ms",
            sa.Integer(),
            nullable=True,
            comment="실제 함수가 돈 시간(밀리초). 진입 전 거절은 NULL이다. 툴 SQL이 느려지는 것을 본다",
        ),
        sa.Column("result_chars", sa.Integer(), nullable=False, comment="결과 문자 수. 오류면 0이다"),
        sa.Column(
            "result",
            sa.Text(),
            nullable=True,
            comment=(
                "결과 본문 전문. 성공이면 툴이 돌려준 JSON 문자열이다. jsonb로 굳히지 않는 것은 "
                "실패 본문이 평문이어서다 — 분석은 error IS NULL 뒤에 result::jsonb를 쓴다"
            ),
        ),
        sa.Column(
            "delivered",
            sa.Boolean(),
            nullable=False,
            comment=(
                "이 결과·오류가 모델 대화에 실제로 돌아갔나. sibling 예외로 ToolNode가 결과를 버리면 "
                "false다 — 결과는 진짜이고 모델만 못 봤다. 실행조차 못 한 것은 error_kind=cancelled다"
            ),
        ),
        sa.Column(
            "error_kind",
            _enum("unknown_tool", "validation", "limit", "execution", "cancelled"),
            nullable=True,
            comment="실패 종류. 오류가 있을 때만 채운다. 오류 문자열을 파싱해 분류하지 않는다",
        ),
        sa.Column(
            "error",
            sa.Text(),
            nullable=True,
            comment=(
                "실패 사유. delivered면 ToolNode가 만든 ToolMessage 본문 그대로이고, "
                "ToolMessage가 없으면 래퍼가 잡은 예외 문자열이다"
            ),
        ),
        sa.CheckConstraint(
            "error_kind IN ('unknown_tool', 'validation', 'limit', 'execution', 'cancelled')",
            name="ck_thesis_tool_call_error_kind",
        ),
        sa.CheckConstraint("(result IS NULL) <> (error IS NULL)", name="ck_thesis_tool_call_result_xor_error"),
        sa.CheckConstraint("(error IS NULL) = (error_kind IS NULL)", name="ck_thesis_tool_call_error_kind_pairs"),
        sa.CheckConstraint("seq > 0 AND round_no > 0", name="ck_thesis_tool_call_positive_order"),
        sa.ForeignKeyConstraint(["llm_run_id"], ["thesis_llm_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("llm_run_id", "seq", name="uq_thesis_tool_call_seq"),
        sa.UniqueConstraint("llm_run_id", "round_no", "tool_call_id", name="uq_thesis_tool_call_id"),
        comment="LLM 대화가 부른 툴 하나. 인자와 결과 전문을 남겨 나중에 패턴과 상관을 잰다",
    )

    op.add_column(
        "thesis",
        sa.Column("llm_run_id", sa.BigInteger(), nullable=True, comment=LLM_RUN_ID_COMMENT),
    )
    op.create_foreign_key(
        "fk_thesis_llm_run_id",
        "thesis",
        "thesis_llm_run",
        ["llm_run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "thesis_outcome",
        sa.Column("narration_run_id", sa.BigInteger(), nullable=True, comment=NARRATION_RUN_ID_COMMENT),
    )
    op.create_foreign_key(
        "fk_thesis_outcome_narration_run_id",
        "thesis_outcome",
        "thesis_llm_run",
        ["narration_run_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade_default() -> None:
    op.drop_constraint("fk_thesis_outcome_narration_run_id", "thesis_outcome", type_="foreignkey")
    op.drop_column("thesis_outcome", "narration_run_id")
    op.drop_constraint("fk_thesis_llm_run_id", "thesis", type_="foreignkey")
    op.drop_column("thesis", "llm_run_id")
    op.drop_table("thesis_tool_call")
    op.drop_table("thesis_llm_run")
