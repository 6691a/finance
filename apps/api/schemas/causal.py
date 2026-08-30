"""주간 사후 인과 그래프의 응답 계약.

**경로 하나가 응답의 한 줄이다.** 사건 → 채널 체인 → 대상이고, 체인은 `market_causal_step`
여러 행이라 배열로 편다 — 화면이 단계마다 조회를 다시 하지 않게 한다.

**실현 등락에는 언제나 단위가 붙는다.** `KTB10Y`의 +7.4bp와 KOSPI의 +10.77%가 한 칸에
들어가면 크기 비교가 조용히 무의미해진다. 저장이 `return_unit`으로 그것을 가르고 있으므로
응답도 그 칸을 함께 낸다 — 숫자만 보내면 화면이 다시 추측한다.
"""

from datetime import date

from pydantic import Field

from apps.api.schemas.common import ApiModel, Page


class CausalPathRow(ApiModel):
    """출발점 하나가 대상 하나에 닿은 경로 하나.

    **출발점은 사건 또는 대상이다.** "US10Y가 내려서 SOX가 올랐다"를 담으려면 앞 경로의
    끝이 다음 경로의 시작이어야 하는데, 그것을 새 사건으로 만들면 `target:US10Y`와
    `event:미국 국채금리 하락`이 다른 노드라 그래프가 거기서 끊긴다. 그래서 `source_kind`가
    둘을 가르고 **한쪽 칸만 채워진다**(DB CHECK가 그것을 강제한다).
    """

    id: int = Field(description="경로 id.")
    week_start: date = Field(description="이 경로가 관찰된 주의 시작일(월요일).")
    source_kind: str = Field(
        description="출발점의 종류(`event` 또는 `target`). 어느 칸이 채워졌는지를 이 값이 말한다."
    )
    event_id: int | None = Field(default=None, description="출발 사건 id. 대상 출발이면 null.")
    event_title: str | None = Field(default=None, description="출발 사건의 제목.")
    event_occurred_on: date | None = Field(default=None, description="사건이 일어난 날.")
    source_target_kind: str | None = Field(
        default=None, description="대상 출발일 때 원인 대상의 종류. 사건 출발이면 null."
    )
    source_target_code: str | None = Field(
        default=None,
        description=(
            "대상 출발일 때 원인 대상의 식별자. **같은 주 다른 경로의 대상이다** — 그래서 "
            "그래프에서 노드가 이어져 다중 홉이 된다."
        ),
    )
    source_sign: str | None = Field(
        default=None, description="원인 대상이 그 주에 움직인 방향(up·down). 사건 출발이면 null."
    )
    target_kind: str = Field(
        description=(
            "대상이 어느 마스터에서 오는지(instrument·index·quote·indicator). "
            "**값의 성격이 아니라 저장소를 가른다** — `US10Y`는 시세, `KTB10Y`는 지표다."
        )
    )
    target_code: str = Field(description="대상 식별자.")
    channels: tuple[str, ...] = Field(
        default=(),
        description="전달 경로 체인. **사건 쪽에서 대상 쪽 순서다**(`position` 오름차순).",
    )
    sign: str = Field(description="이 경로가 대상을 민 방향(up·down).")
    confidence: str = Field(
        description=(
            "observed는 근거 문서가 그 방향을 직접 말함, endpoint_observed는 양 끝 값이 그렇게 "
            "움직임, plausible은 해석. **셋 다 인과의 증명이 아니다.** `endpoint_observed`는 "
            "대상에서 출발한 경로만 가질 수 있다 — 사건 출발은 원인 쪽이 문서라 값으로 대조할 "
            "것이 없다."
        )
    )
    reasoning: str = Field(description="이 경로를 설명하는 한 문장. 모델이 만든다.")
    return_week_change: float = Field(description="그 주 대상 변화. 단위는 `return_unit`.")
    return_t1_change: float = Field(description="주 종료 다음 거래일까지의 변화.")
    return_t5_change: float = Field(description="주 종료 +5 거래일까지의 변화.")
    return_unit: str = Field(
        description="실현 등락 셋의 단위(percent·basis_point). **숫자만 읽으면 안 된다.**"
    )
    llm_run_id: int | None = Field(
        default=None, description="이 경로를 만든 실행 원장 id. 없으면 원장 이전의 행이다."
    )


CausalPathList = Page[CausalPathRow]


class CausalEvidenceRow(ApiModel):
    """경로 하나가 근거로 든 후보 하나.

    **`ref`는 `<kind>:<id>` 문자열이고 외래키가 없다.** 근거가 문서·공시·기술적 신호 셋에
    흩어져 있어 걸 대상이 하나가 아니다. 그래서 응답이 그 규약을 풀어 준다 — 화면이 매번
    문자열을 쪼개면 규약이 두 곳에 생긴다.
    """

    path_id: int = Field(description="이 근거가 붙은 경로.")
    ref: str = Field(description="후보 식별자 원문(`document:84026`).")
    kind: str = Field(description="근거의 종류(document·disclosure·technical_signal).")
    title: str | None = Field(
        default=None,
        description=(
            "읽을 수 있는 이름. **문서만 채운다** — `document:189`만 보이면 사람이 못 읽는다. "
            "다른 종류는 화면이 식별자를 그대로 보인다."
        ),
    )
    url: str | None = Field(
        default=None,
        description="원문으로 가는 길. 문서는 화면 상세 경로, 공시는 DART 뷰어다.",
    )


class CausalPathDetail(ApiModel):
    """경로 하나와 **그 주의 경로 전부**.

    상세를 경로 하나로만 내면 화면이 그릴 것이 직선 하나뿐이다. 이 그래프의 값어치는
    한 주의 경로들이 노드를 공유해 사슬을 만드는 모양에 있으므로 주 전체를 함께 낸다 —
    화면이 대상마다 다시 묻지 않게 하는 것도 같은 이유다.
    """

    path: CausalPathRow
    siblings: tuple[CausalPathRow, ...] = Field(
        default=(),
        description=(
            "**같은 주의 경로 전부.** 자기 자신을 포함한다. 사건 단위가 아니라 주 단위인 이유는 "
            "대상이 다시 원인이 되기 때문이다 — `VIX → NASDAQ100_FUT → SOX → 005930`은 경로 "
            "넷이고 사건으로 묶으면 그 사슬이 끊어진 채로 보인다."
        ),
    )
    evidence: tuple[CausalEvidenceRow, ...] = Field(
        default=(),
        description=(
            "형제 경로들이 인용한 근거 전부. `path_id`로 갈라 읽는다. **`confidence`가 옳은지"
            "되짚는 자리다** — 이 표가 없던 동안은 볼 방법이 없었다."
        ),
    )


class CausalEventRow(ApiModel):
    """그 주에 실제로 일어난 일 하나. 그래프의 출발 노드다."""

    id: int = Field(description="사건 id.")
    title: str = Field(description="사건 제목.")
    occurred_on: date = Field(description="사건이 일어난 날.")
    first_seen_week: date = Field(description="이 사건이 처음 등장한 주.")
    paths: int = Field(default=0, description="이 사건에서 뻗은 경로 수.")


CausalEventList = Page[CausalEventRow]


class CausalChannelRow(ApiModel):
    """사건이 대상에 닿은 전달 경로 하나. 그래프의 가운데 노드다."""

    id: int = Field(description="채널 id.")
    name: str = Field(description="채널 이름.")
    first_seen_week: date = Field(description="이 채널이 처음 등장한 주.")
    steps: int = Field(default=0, description="이 채널을 거친 단계 수. 주가 쌓이면 는다.")


CausalChannelList = Page[CausalChannelRow]
