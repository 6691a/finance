"""요인 관계와 메모의 응답 계약. 원본은 Neo4j다.

**가중치는 화면이 계산하지 않는다.** 서비스가 감쇠 평균을 접어 내려 준다 — 같은 식이
프롬프트에도 실리므로 두 곳이 다른 값을 말하면 안 된다.

**`weight`와 `recent_signs`를 함께 준다.** 가중치 하나로는 "오래 일관된 -0.5"와 "막
뒤집히는 중인 -0.15"가 구분되지 않는다(설계 `kospi-forecast.md` §3.3).

설계는 `docs/analysis/kospi-forecast-web.md` §1.2다.
"""

from datetime import date

from pydantic import Field

from apps.api.schemas.common import ApiModel, Page


class RelationItem(ApiModel):
    """요인 하나의 관계. **관측이 0이어도 행이 있다** — 빈 칸은 "관계 없음"으로 읽힌다."""

    factor: str = Field(description="요인 코드.")
    label: str = Field(description="한국어 이름.")
    weight: float = Field(
        description=(
            "-1~1. 양수면 같이 움직였고 음수면 반대다. **최근 관측에 기울어 있다**"
            "(반감기 5일). 관측이 없으면 0이고 그때 뜻은 \"모른다\"다."
        )
    )
    n_obs: int = Field(default=0, description="가중치에 들어간 관측 수. **0이면 weight를 읽지 않는다.**")
    last_date: date | None = Field(default=None, description="마지막 관측일. 없으면 null이다.")
    last_note: str = Field(default="", description="마지막 관찰 문장.")
    recent_signs: tuple[str, ...] = Field(
        default=(),
        description="감쇠 없는 최근 부호 셋(same·inverse). 가중치와 어긋나면 관계가 바뀌는 중이다.",
    )
    url: str = Field(description="이 요인의 관측 목록 경로.")


RelationList = Page[RelationItem]


class ObservationItem(ApiModel):
    """관측 하나. 장후 관찰이 하루에 요인당 최대 하나를 남긴다."""

    factor: str = Field(description="요인 코드.")
    observed_on: date = Field(description="관찰한 거래일(KST).")
    sign: str = Field(description="same이면 코스피와 같은 방향, inverse면 반대다.")
    strength: int = Field(description="1·2·3. **3이 \"주도했다\"**이고 1은 부차적이다.")
    note: str = Field(default="", description="그때의 관찰 문장.")
    weight: float = Field(
        description="오늘 기준 이 관측 하나의 무게(0~1). 나이가 반감기마다 절반이 된다."
    )
    llm_run_id: int | None = Field(default=None, description="이 관측을 낸 대화 id.")
    llm_run_url: str | None = Field(default=None, description="그 대화의 상세 경로.")


ObservationList = Page[ObservationItem]


class RelationNode(ApiModel):
    """그림의 노드 하나. 요인 열일곱과 코스피 하나다."""

    id: str = Field(description="`factor:<코드>` 또는 `index:KOSPI`.")
    kind: str = Field(description="factor·index.")
    label: str = Field(description="화면에 쓰는 이름.")
    n_obs: int = Field(default=0, description="관측 수. 노드 크기가 된다.")


class RelationEdge(ApiModel):
    """그림의 엣지 하나. **요인 하나가 코스피 하나를 가리키는 깊이 1이다.**"""

    source: str = Field(description="요인 노드 id.")
    target: str = Field(description="코스피 노드 id.")
    weight: float = Field(description="-1~1. 색이 부호이고 두께가 크기다.")
    n_obs: int = Field(default=0, description="이 엣지가 접은 관측 수.")


class RelationGraph(ApiModel):
    """관계 그림. 관측이 없는 요인은 노드만 있고 엣지가 없다."""

    as_of_date: date = Field(description="이 그림의 기준일(KST).")
    nodes: tuple[RelationNode, ...] = ()
    edges: tuple[RelationEdge, ...] = ()


class MemoryItem(ApiModel):
    """메모 하나. **내린 것도 지우지 않는다** — 왜 지웠는지가 남아야 한다."""

    id: int = Field(description="메모 id. 전망의 이유가 이 값으로 인용한다.")
    created_on: date = Field(description="만든 날(KST).")
    text: str = Field(description="메모 문장. 200자 이내다.")
    factor: str | None = Field(default=None, description="연결된 요인. 없을 수 있다.")
    verify_count: int = Field(default=0, description="유지 판정을 받은 횟수.")
    unreviewed_count: int = Field(
        default=0, description="검토에서 빠진 횟수. 둘이 되면 코드가 내린다."
    )
    last_verified_on: date | None = Field(default=None, description="마지막 유지 판정일.")
    retired_on: date | None = Field(default=None, description="내린 날. null이면 활성이다.")
    retire_reason: str | None = Field(
        default=None,
        description="내린 이유. 모델 판정(drop)과 코드 판정(unreviewed·expired)이 섞여 있다.",
    )
    llm_run_id: int | None = Field(default=None, description="이 메모를 쓴 대화 id.")
    llm_run_url: str | None = Field(default=None, description="그 대화의 상세 경로.")


MemoryList = Page[MemoryItem]

