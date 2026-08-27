"""add market causal graph tables

Revision ID: b4e91c72a3d5
Revises: b7f4c2a91d38
Create Date: 2026-08-27 17:20:00.000000

주간 사후 인과 그래프 — 사건, 전달 경로, 그 둘을 대상에 잇는 경로와 단계.

설계는 `docs/analysis/market-causal-graph.md`다. 8주 프로토타입으로 어휘 수렴을 확인한 뒤의
계약이고, 여기서 가장 중요한 것은 **마스터 둘의 자연키**다.

- `market_event`는 `(title, occurred_on)`이다. 같은 제목이 다른 날 다시 일어나면 다른
  사건이기 때문이다 — `미국 반도체 지수 하락`이 8주에 두 번 나왔다.
- `market_channel`은 `(name)` 하나다. 채널에는 날짜가 없다. `할인율`은 언제 나와도 같은
  `할인율`이고, **그 겹침이 서로 다른 주의 경로를 사슬로 잇는다.** 이 설계에서 다중 홉이
  생기는 유일한 자리다.
- `market_causal_path`의 자연키에 `chain_key`가 들어간다. 같은 사건이 같은 대상에 서로 다른
  경로로 닿는 일이 실제로 있어서다(금리 인상이 `할인율`로는 은행주를 누르고 `예대마진`으로는
  올린다). 자연키가 그것을 못 담으면 두 번째 경로가 `ON CONFLICT DO NOTHING`에 조용히
  삼켜지고, 조용한 손실은 나중에 자연키를 늘려도 되돌릴 수 없다.

원장은 새로 만들지 않고 `thesis_llm_run`에 종류를 하나 더한다. 그 목적이 "툴 호출 패턴과
결과의 상관을 재는 것"이고 여기에도 그대로 적용되기 때문이다. 다만 **주간 분석에는 슬롯이
없어** `run_slot`을 nullable로 풀고, 나머지 종류가 슬롯을 빠뜨리는 것은 조합 CHECK로 막는다.

**수기 리비전이다.** `config.yaml`이 운영 DB를 가리켜 autogenerate를 돌리지 않는다.
모델(`apps/models/analysis/causal.py`, `thesis.py`)과 여기의 컬럼 주석은 **글자 그대로** 같아야
한다. 다르면 다음 autogenerate가 매번 차이를 만든다.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4e91c72a3d5"
down_revision: str | Sequence[str] | None = "b7f4c2a91d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 한 경로가 거치는 전달 단계의 상한. 모델의 `MAX_CHAIN`과 같은 값이다.
MAX_CHAIN = 3

# 원장이 받는 대화 종류. `causal`이 이번에 는 값이다.
LLM_RUN_KINDS = ("forecast", "review", "nxt_review", "narration", "causal")
LLM_RUN_KINDS_BEFORE = ("forecast", "review", "nxt_review", "narration")


def upgrade(engine_name: str) -> None:
    _run(f"upgrade_{engine_name}")


def downgrade(engine_name: str) -> None:
    _run(f"downgrade_{engine_name}")


def _run(name: str) -> None:
    # 이 리비전이 다루지 않는 별칭은 함수가 없고, 없으면 그 별칭이 할 일이 없다는 뜻이다.
    operations = globals().get(name)
    if operations is not None:
        operations()


def _entity_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False, comment="레코드 고유 식별자"),
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
            comment="레코드 최종 수정 시각(UTC)",
        ),
    ]


def _in_list(column: str, values: Sequence[str]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def upgrade_default() -> None:
    op.create_table(
        "market_event",
        *_entity_columns(),
        sa.Column(
            "title",
            sa.Text(),
            nullable=False,
            comment="사건 한 줄. 예: 한국은행 기준금리 25bp 인상. LLM이 만든 자유 텍스트다",
        ),
        sa.Column(
            "occurred_on",
            sa.Date(),
            nullable=False,
            comment=(
                "사건이 일어난 날(KST 달력일). 프롬프트 후보를 최근 몇 주로 좁히는 기준이고, "
                "분석한 주(market_causal_path.week_start)보다 미래일 수 없다"
            ),
        ),
        sa.Column(
            "first_seen_week",
            sa.Date(),
            nullable=False,
            comment="이 사건을 처음 만든 분석 주의 월요일(KST). 어휘가 언제 자랐는지를 본다",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("title", "occurred_on", name="uq_market_event_natural_key"),
        comment="주간 인과 그래프의 사건 노드. 제목과 발생일이 자연키다",
    )

    op.create_table(
        "market_channel",
        *_entity_columns(),
        sa.Column(
            "name",
            sa.Text(),
            nullable=False,
            comment="경로 이름. 예: 할인율, 위험선호, 수급. 방향·지역·종목을 넣지 않는다",
        ),
        sa.Column(
            "first_seen_week",
            sa.Date(),
            nullable=False,
            comment="이 경로를 처음 만든 분석 주의 월요일(KST). 어휘 수렴을 관측하는 값이다",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_market_channel_natural_key"),
        comment="주간 인과 그래프의 전달 경로 노드. 이름 하나가 자연키다",
    )

    op.create_table(
        "market_causal_path",
        *_entity_columns(),
        sa.Column(
            "week_start",
            sa.Date(),
            nullable=False,
            comment=(
                "분석한 주의 월요일(KST). 사건이 일어난 주이며 실현 등락 셋의 기준이기도 하다. "
                "사건 자체는 이 주보다 앞설 수 있다"
            ),
        ),
        sa.Column(
            "event_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 경로의 출발 사건. 지우면 그래프가 끊기므로 RESTRICT다",
        ),
        sa.Column(
            "target_kind",
            sa.String(length=20),
            nullable=False,
            comment="대상이 어느 마스터에서 오는지(instrument·index·quote·indicator)",
        ),
        sa.Column(
            "target_code",
            sa.Text(),
            nullable=False,
            comment="대상 식별자. 예: 005930, KOSPI, USDKRW, KTB10Y. 마스터 밖 값은 저장 전에 버린다",
        ),
        sa.Column(
            "chain_key",
            sa.Text(),
            nullable=False,
            comment=(
                "단계의 channel_id를 position 순서대로 '>'로 이은 문자열. "
                "market_causal_step의 비정규화이고 자연키를 헤더 한 행에 담기 위해 둔다"
            ),
        ),
        sa.Column(
            "sign",
            sa.String(length=20),
            nullable=False,
            comment="모델이 주장한 방향(up 또는 down). 실현 등락과 엇갈려도 고치지 않는다",
        ),
        sa.Column(
            "confidence",
            sa.String(length=20),
            nullable=False,
            comment=(
                "observed는 같은 기간에 함께 관찰됨, plausible은 해석. 둘 다 인과의 증명이 아니다"
            ),
        ),
        sa.Column(
            "reasoning",
            sa.Text(),
            nullable=False,
            comment="이 경로를 설명하는 한 문장. 모델이 만든다",
        ),
        sa.Column(
            "return_week_pct",
            sa.Numeric(precision=10, scale=4),
            nullable=False,
            comment="그 주 대상 등락률(%). SQL이 계산한다. 경로가 작용했다고 주장하는 창이다",
        ),
        sa.Column(
            "return_t1_pct",
            sa.Numeric(precision=10, scale=4),
            nullable=False,
            comment="주 종료 다음 KRX 거래일까지의 등락률(%). SQL이 계산한다",
        ),
        sa.Column(
            "return_t5_pct",
            sa.Numeric(precision=10, scale=4),
            nullable=False,
            comment="주 종료 +5 KRX 거래일까지의 등락률(%). SQL이 계산한다",
        ),
        sa.Column(
            "input_hash",
            sa.Text(),
            nullable=False,
            comment=(
                "이 경로를 만든 실행의 입력 해시(주·대상·후보 ref·프롬프트 판). "
                "재실행 판정이 아니라 감사 값이라 자연키에 넣지 않는다"
            ),
        ),
        sa.Column(
            "llm_run_id",
            sa.BigInteger(),
            nullable=True,
            comment="이 경로를 만든 LLM 대화. 원장이 지워져도 경로는 남는다",
        ),
        sa.ForeignKeyConstraint(["event_id"], ["market_event.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["llm_run_id"], ["thesis_llm_run.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "week_start",
            "event_id",
            "target_kind",
            "target_code",
            "chain_key",
            name="uq_market_causal_path_natural_key",
        ),
        sa.CheckConstraint(_in_list("sign", ("up", "down")), name="ck_market_causal_path_sign"),
        sa.CheckConstraint(
            _in_list("confidence", ("observed", "plausible")),
            name="ck_market_causal_path_confidence",
        ),
        sa.CheckConstraint(
            _in_list("target_kind", ("instrument", "index", "quote", "indicator")),
            name="ck_market_causal_path_target_kind",
        ),
        comment="주간 인과 그래프의 경로 하나. 사건에서 대상까지의 주장과 실현 등락이다",
    )

    op.create_table(
        "market_causal_step",
        *_entity_columns(),
        sa.Column(
            "path_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 단계가 속한 경로. 헤더가 지워지면 함께 지운다",
        ),
        sa.Column(
            "position",
            sa.Integer(),
            nullable=False,
            comment=f"단계 순서. 1이 사건에 가장 가깝고 최대 {MAX_CHAIN}이다. 빈 곳 없이 채운다",
        ),
        sa.Column(
            "channel_id",
            sa.BigInteger(),
            nullable=False,
            comment="이 단계의 전달 경로. 지우면 그래프가 끊기므로 RESTRICT다",
        ),
        sa.ForeignKeyConstraint(["path_id"], ["market_causal_path.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["channel_id"], ["market_channel.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path_id", "position", name="uq_market_causal_step_natural_key"),
        sa.CheckConstraint(
            f"position BETWEEN 1 AND {MAX_CHAIN}",
            name="ck_market_causal_step_position",
        ),
        comment="주간 인과 그래프 경로의 전달 단계. 순서가 있는 자식 행이다",
    )

    # 원장은 나누지 않고 종류를 하나 더한다. 슬롯이 없는 유일한 종류라 run_slot을 푼다.
    op.drop_constraint("ck_thesis_llm_run_kind", "thesis_llm_run", type_="check")
    op.create_check_constraint(
        "ck_thesis_llm_run_kind", "thesis_llm_run", _in_list("kind", LLM_RUN_KINDS)
    )
    op.alter_column("thesis_llm_run", "run_slot", existing_type=sa.String(length=20), nullable=True)
    op.create_check_constraint(
        "ck_thesis_llm_run_slot_shape",
        "thesis_llm_run",
        "(kind = 'causal' AND run_slot IS NULL)"
        " OR (kind <> 'causal' AND run_slot IS NOT NULL)",
    )
    op.alter_column(
        "thesis_llm_run",
        "run_slot",
        existing_type=sa.String(length=20),
        comment=(
            "대상 슬롯. 해설이면 원 추론의 슬롯이다. "
            "주간 인과 그래프(kind='causal')만 슬롯이 없어 NULL이고 CHECK가 그것을 강제한다"
        ),
        existing_comment="대상 슬롯. 해설이면 원 추론의 슬롯이다",
        existing_nullable=True,
    )


def downgrade_default() -> None:
    op.alter_column(
        "thesis_llm_run",
        "run_slot",
        existing_type=sa.String(length=20),
        comment="대상 슬롯. 해설이면 원 추론의 슬롯이다",
        existing_comment=(
            "대상 슬롯. 해설이면 원 추론의 슬롯이다. "
            "주간 인과 그래프(kind='causal')만 슬롯이 없어 NULL이고 CHECK가 그것을 강제한다"
        ),
        existing_nullable=True,
    )
    op.drop_constraint("ck_thesis_llm_run_slot_shape", "thesis_llm_run", type_="check")
    # causal 대화가 남아 있으면 NOT NULL로 되돌릴 수 없다. 그 행을 먼저 지운다.
    op.execute("DELETE FROM thesis_llm_run WHERE kind = 'causal'")
    op.alter_column(
        "thesis_llm_run", "run_slot", existing_type=sa.String(length=20), nullable=False
    )
    op.drop_constraint("ck_thesis_llm_run_kind", "thesis_llm_run", type_="check")
    op.create_check_constraint(
        "ck_thesis_llm_run_kind", "thesis_llm_run", _in_list("kind", LLM_RUN_KINDS_BEFORE)
    )

    op.drop_table("market_causal_step")
    op.drop_table("market_causal_path")
    op.drop_table("market_channel")
    op.drop_table("market_event")
