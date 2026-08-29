"""프롬프트 조립과 답변 검증. LangChain을 import하므로 `test_causal.py`와 파일을 나눈다.

계약은 docs/analysis/market-causal-graph.md 4·5절이다. 실제 모델은 부르지 않는다.
"""

from datetime import UTC, date, datetime

from modules.causal import domain, generation


def _candidates() -> domain.CandidateSet:
    return domain.CandidateSet(
        documents=(
            domain.DocumentCandidate(
                ref="document:84026",
                target_code="005930",
                title="삼성전자 반도체 수출 급증",
                summary="요약",
                source_slug="yonhap",
                published_at=datetime(2026, 8, 14, 1, 0, tzinfo=UTC),
                value_score=8,
                assessed_direction="up",
            ),
        ),
        disclosures=(
            domain.DisclosureCandidate(
                ref="disclosure:20260819000123",
                target_code="005930",
                company_name="삼성전자",
                report_name="자기주식취득결정",
                receipt_date=date(2026, 8, 19),
                body="취득예정금액 3,000,000,000,000원 취득목적 주주가치 제고 취득방법 장내매수",
            ),
        ),
    )


class TestVocabularyBlock:
    """자라는 어휘의 후보 목록. 이 블록이 그래프를 잇는 유일한 장치다(설계 §4)."""

    def test_the_first_week_says_the_vocabulary_is_empty(self) -> None:
        """첫 주는 목록이 비어 있고, 전부 새로 만드는 것이 정상이다."""
        block = generation.vocabulary_block(events=(), channels=())

        assert "첫 주" in block
        assert "e:" not in block

    def test_existing_nodes_are_offered_with_their_ids(self) -> None:
        """모델은 id로 고른다. 이름만 주면 같은 것을 다시 만든다."""
        block = generation.vocabulary_block(
            events=(
                domain.EventOption(
                    node_id="e:812",
                    title="한은 기준금리 인상",
                    occurred_on=date(2026, 8, 19),
                ),
            ),
            channels=(domain.ChannelOption(node_id="c:1", name="할인율"),),
        )

        assert "e:812" in block
        assert "한은 기준금리 인상" in block
        assert "c:1" in block
        assert "할인율" in block


class TestCandidateBlock:
    def test_every_candidate_shows_its_ref(self) -> None:
        """모델이 인용할 수 있는 것이 무엇인지는 ref로만 말한다."""
        block = generation.candidate_block(_candidates())

        assert "document:84026" in block
        assert "disclosure:20260819000123" in block

    def test_an_empty_week_says_so_instead_of_showing_nothing(self) -> None:
        """빈 목록을 그냥 두면 모델이 ref를 지어낸다. 없다는 것을 명시한다."""
        block = generation.candidate_block(domain.CandidateSet())

        assert "없음" in block
        assert "빈 목록" in block


class TestVerifyPaths:
    """답변 검증. 목록 밖 ref와 마스터 밖 대상은 저장 전에 버린다(설계 §5.3)."""

    def _answer(self, **overrides) -> generation.CausalPathAnswer:
        base = {
            "event": generation.NodeChoice(new_name="한은 기준금리 인상"),
            "event_date": "2026-08-19",
            "channels": [generation.NodeChoice(new_name="할인율")],
            "target_kind": "instrument",
            "target_code": "005930",
            "sign": "down",
            "confidence": "observed",
            "reasoning": "금리 인상이 할인율을 높였다",
            "evidence_refs": ["document:84026"],
        }
        return generation.CausalPathAnswer(**(base | overrides))

    def test_refs_outside_the_registry_are_dropped(self) -> None:
        path = self._answer(evidence_refs=["document:84026", "document:99999"])

        kept = generation.verify_paths([path], _candidates(), {"005930"})

        assert kept[0].evidence_refs == ("document:84026",)

    def test_a_path_to_an_unknown_target_is_dropped_entirely(self) -> None:
        """마스터 밖 대상은 저장할 수 없다. 그 경로를 통째로 버린다."""
        path = self._answer(target_code="999999")

        assert generation.verify_paths([path], _candidates(), {"005930"}) == ()

    def test_a_path_with_no_surviving_ref_is_kept(self) -> None:
        """근거가 없어도 경로 자체는 남는다 — 실현 등락만으로 설명되는 주가 있다."""
        path = self._answer(evidence_refs=["document:99999"])

        kept = generation.verify_paths([path], _candidates(), {"005930"})

        assert len(kept) == 1
        assert kept[0].evidence_refs == ()

    def test_a_chain_longer_than_the_limit_is_dropped(self) -> None:
        """상한은 코드가 막는다. 프롬프트에만 적으면 모델이 넘길 때 막을 것이 없다."""
        path = self._answer(
            channels=[
                generation.NodeChoice(new_name=f"c{n}") for n in range(domain.MAX_CHAIN + 1)
            ]
        )

        assert generation.verify_paths([path], _candidates(), {"005930"}) == ()

    def test_an_empty_chain_is_dropped(self) -> None:
        """경로가 없으면 사건과 대상만 남아 그래프가 아니다."""
        path = self._answer(channels=[])

        assert generation.verify_paths([path], _candidates(), {"005930"}) == ()


class FakeModel:
    """`llm.invoke`가 받는 모델 자리. 준비된 답을 순서대로 준다.

    `bind`는 `llm.invoke`가 `response_format`을 붙이는 자리라 자기 자신을 돌려준다 —
    스키마가 실제로 걸렸는지는 `bound_with`가 증언한다.
    """

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[list] = []
        self.bound_with: list[dict] = []
        self.configured_with: list[dict] = []

    def bind(self, **kwargs):
        self.bound_with.append(kwargs)
        return self

    def with_config(self, config):
        self.configured_with.append(config)
        return self

    def invoke(self, messages, **kwargs):
        from langchain_core.messages import AIMessage

        self.calls.append(list(messages))
        return AIMessage(content=self._replies.pop(0))


ONE_PATH = """
{"paths": [{"event": {"existing_id": "", "new_name": "한은 기준금리 인상"},
  "event_date": "2026-08-19",
  "channels": [{"existing_id": "", "new_name": "할인율"},
               {"existing_id": "", "new_name": "밸류에이션"}],
  "target_kind": "instrument", "target_code": "005930",
  "sign": "down", "confidence": "observed",
  "reasoning": "금리 인상이 할인율을 높여 밸류에이션을 눌렀다",
  "evidence_refs": ["document:84026"]}]}
"""

NO_PATHS = '{"paths": []}'


WINDOW = domain.window_for(date(2026, 8, 10))


def _returns() -> dict[str, domain.TargetReturns]:
    return {
        "005930": domain.TargetReturns(
            week=19.35, t1=-2.18, t5=-6.37, unit=domain.CausalReturnUnit.PERCENT
        ),
        "KTB10Y": domain.TargetReturns(
            week=7.4, t1=6.9, t5=2.2, unit=domain.CausalReturnUnit.BASIS_POINT
        ),
    }


def _build(model: FakeModel) -> tuple[generation.VerifiedPath, ...]:
    return _build_with(generation.CausalBuilder(model))


def _build_with(builder: generation.CausalBuilder) -> tuple[generation.VerifiedPath, ...]:
    return builder.build(
        window=WINDOW,
        returns=_returns(),
        found=_candidates(),
        events=(),
        channels=(),
        targets=(
            domain.CausalTarget(kind=domain.CausalTargetKind.INSTRUMENT, code="005930"),
            domain.CausalTarget(kind=domain.CausalTargetKind.INDICATOR, code="KTB10Y"),
            domain.CausalTarget(kind=domain.CausalTargetKind.INDEX, code="KOSPI"),
        ),
    )


class TestTheVocabularyBlockSaysHowToPickEvents:
    """사건 후보를 그냥 나열하면 모델이 지난주 것을 이번 주 등락에 갖다 쓴다."""

    def test_the_block_tells_the_model_to_prefer_this_week(self) -> None:
        block = generation.vocabulary_block(
            events=(
                domain.EventOption(
                    node_id="e:1", title="미국 고용 둔화 확인", occurred_on=date(2026, 8, 7)
                ),
            ),
            channels=(domain.ChannelOption(node_id="c:1", name="금리 기대"),),
        )

        assert "대상 주" in block
        assert "e:1" in block


class TestResponsesApiContent:
    """**Responses API는 `content`가 블록 리스트다.** 문자열이 아니다.

    2026-08-28에 툴을 붙이며 `use_responses_api=True`로 옮겼더니 `str(reply.content)`가
    파이썬 repr를 만들어 파싱이 죽었다 — `Invalid JSON: key must be a string`. 실제 모델이
    아니면 못 잡는 사고다.
    """

    def test_a_block_list_is_flattened_to_its_text(self) -> None:
        reply = type("Reply", (), {"content": [
            {"type": "reasoning", "id": "rs_1"},
            {"type": "text", "text": ONE_PATH},
        ]})()

        assert generation.reply_text(reply) == ONE_PATH

    def test_a_plain_string_passes_through(self) -> None:
        reply = type("Reply", (), {"content": ONE_PATH})()

        assert generation.reply_text(reply) == ONE_PATH


class TestTheFlowIsAGraph:
    """흐름 제어는 LangGraph다(CLAUDE.md). `if`로 교정을 재요청하지 않는다.

    2026-08-28 운영 실행의 LangSmith run이 이름 `ChatOpenAI`에 `tags` 빈 목록이었다.
    노드 이름이 트레이스에 남는 것이 이 규칙의 목적이고, 저장소의 다른 흐름 여섯이
    이미 같은 모양(`call` → 조건부 `repair` → `call`)이다.
    """

    def test_the_flow_investigates_before_it_answers(self) -> None:
        """툴이 붙으면 앞에 두 노드가 는다(`thesis/generation`과 같은 모양)."""
        builder = generation.CausalBuilder(FakeModel([]))

        nodes = set(builder._graph.get_graph().nodes)

        assert {"investigate", "tools", "answer", "repair"} <= nodes

    def test_the_tool_node_only_swallows_limit_errors(self) -> None:
        """기본값(`True`)은 DB 연결 끊김을 "결과 없음"으로 위장한다. 모델이 고쳐 부를 수
        있는 것만 `ToolMessage`가 되어야 한다."""
        from modules.causal.toolbox import ToolLimitExceeded

        builder = generation.CausalBuilder(FakeModel([]))

        assert builder._tool_node._handle_tool_errors == (ToolLimitExceeded,)

    def test_the_graph_run_carries_the_week(self) -> None:
        """이름은 그래프 실행 하나에만 붙인다. 호출마다 손으로 붙이면 그래프가 없다는 뜻이다."""
        model = FakeModel([ONE_PATH])
        seen: list[dict] = []
        builder = generation.CausalBuilder(model)
        original = builder._graph.invoke
        builder._graph = _Spy(original, seen)

        _build_with(builder)

        config = seen[0]
        assert config["run_name"] == "causal 2026-08-10"
        assert "causal" in config["tags"]
        assert config["metadata"]["week_start"] == "2026-08-10"
        assert config["metadata"]["prompt_version"] == domain.PROMPT_VERSION

    def test_a_repaired_run_calls_the_model_twice(self) -> None:
        model = FakeModel([NO_PATHS, ONE_PATH])

        paths = _build(model)

        assert len(model.calls) == 2
        assert len(paths) == 1


class _Spy:
    """그래프 실행에 어떤 config가 갔는지만 본다."""

    def __init__(self, invoke, seen: list[dict]) -> None:
        self._invoke = invoke
        self._seen = seen

    def invoke(self, state, config=None):
        self._seen.append(config)
        return self._invoke(state, config=config)


class TestCausalBuilder:
    """한 대화가 한 주를 되짚는다. 대상 아홉을 한 번에 본다(설계 §2)."""

    WINDOW = WINDOW

    _returns = staticmethod(_returns)
    _build = staticmethod(_build)

    def test_a_verified_chain_survives_the_round_trip(self) -> None:
        paths = self._build(FakeModel([ONE_PATH]))

        assert len(paths) == 1
        assert [choice.new_name for choice in paths[0].channels] == ["할인율", "밸류에이션"]
        assert paths[0].evidence_refs == ("document:84026",)

    def test_the_prompt_shows_returns_with_their_unit(self) -> None:
        """가격 퍼센트와 금리 bp가 한 칸에 못 들어간다. 모델이 그것을 알아야 한다."""
        model = FakeModel([ONE_PATH])
        self._build(model)

        prompt = model.calls[0][-1].content

        assert "19.35%" in prompt
        assert "7.4bp" in prompt

    def test_targets_without_returns_are_not_offered(self) -> None:
        """실현 등락이 없는 대상은 저장할 수 없다. 프롬프트에도 보여 주지 않는다."""
        model = FakeModel([ONE_PATH])
        self._build(model)

        system = model.calls[0][0].content

        assert "005930" in system
        assert "KOSPI" not in system  # returns에 없다

    def test_an_empty_answer_is_asked_once_more(self) -> None:
        """후보가 있는데 경로를 하나도 안 내면 한 번 다시 묻는다.

        교정에는 **무엇이 잘못됐는지**를 싣는다 — 사유 없는 교정을 받은 모델이 같은 답을
        다시 내는 것을 thesis 판 11에서 이미 봤다.
        """
        model = FakeModel([NO_PATHS, ONE_PATH])

        paths = self._build(model)

        assert len(paths) == 1
        assert len(model.calls) == 2
        assert "경로" in model.calls[1][-1].content

    def test_it_gives_up_after_one_repair(self) -> None:
        """두 번째도 비면 그 주는 경로가 없는 것이다. 무한히 조르지 않는다."""
        model = FakeModel([NO_PATHS, NO_PATHS])

        assert self._build(model) == ()
        assert len(model.calls) == 2


def test_the_response_schema_never_puts_description_next_to_a_ref():
    """중첩 모델에 `Field(description=...)`을 붙이면 스키마가 `$ref` 옆에 `description`을
    두는데 OpenAI가 그것을 거절한다.

        Invalid schema for response_format: $ref cannot have keywords {'description'}

    **가짜 모델로는 절대 안 잡힌다.** 2026-08-27 개발 DB 실행에서 실제로 터졌다.
    """

    def offenders(node, path=""):
        found = []
        if isinstance(node, dict):
            if "$ref" in node and len(node) > 1:
                found.append(f"{path}: {sorted(set(node) - {'$ref'})}")
            for key, value in node.items():
                found += offenders(value, f"{path}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                found += offenders(value, f"{path}[{index}]")
        return found

    schema = generation.response_format(generation.CausalAnswer, "market_causal_paths")

    assert not offenders(schema), offenders(schema)
