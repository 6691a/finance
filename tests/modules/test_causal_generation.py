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

# 링커가 "이을 것이 없다"고 답한 것. **0건이 정상 답이다** — 그래서 `link` 뒤에 교정이 없다.
NO_LINKS = '{"paths": []}'

# **출발점이 `ONE_PATH`가 닿은 대상이어야 한다**(설계 §11.4). 그것이 "한 홉 더 나가는"
# 자리라는 조건 그 자체다.
ONE_LINK = """
{"paths": [{"source_target_code": "005930", "source_target_kind": "instrument",
  "source_sign": "down", "source_date": "2026-08-11",
  "channels": ["수급"],
  "target_kind": "indicator", "target_code": "KTB10Y", "target_date": "2026-08-12",
  "sign": "up", "confidence": "endpoint_observed",
  "reasoning": "8월 11일 대형주가 밀린 뒤 12일 국채금리가 올랐다",
  "evidence_refs": []}]}
"""


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
    return _build_with(generation.CausalBuilder(model)).paths


def _build_with(builder: generation.CausalBuilder, prices=None):
    """`build`는 이제 경로와 대상→대상 연결을 쌍으로 돌려준다(설계 §11.4)."""
    return builder.build(
        prices=prices,
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
        model = FakeModel([ONE_PATH, NO_LINKS])
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

    def test_a_repaired_run_calls_the_model_twice_then_links(self) -> None:
        """교정은 `answer`의 것이고, 답이 나온 뒤에 `link`가 한 번 더 붙는다(설계 §11.3)."""
        model = FakeModel([NO_PATHS, ONE_PATH, NO_LINKS])

        paths = _build(model)

        assert len(model.calls) == 3
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
        paths = self._build(FakeModel([ONE_PATH, NO_LINKS]))

        assert len(paths) == 1
        assert [choice.new_name for choice in paths[0].channels] == ["할인율", "밸류에이션"]
        assert paths[0].evidence_refs == ("document:84026",)

    def test_the_prompt_shows_returns_with_their_unit(self) -> None:
        """가격 퍼센트와 금리 bp가 한 칸에 못 들어간다. 모델이 그것을 알아야 한다."""
        model = FakeModel([ONE_PATH, NO_LINKS])
        self._build(model)

        prompt = model.calls[0][-1].content

        assert "19.35%" in prompt
        assert "7.4bp" in prompt

    def test_targets_without_returns_are_not_offered(self) -> None:
        """실현 등락이 없는 대상은 저장할 수 없다. 프롬프트에도 보여 주지 않는다."""
        model = FakeModel([ONE_PATH, NO_LINKS])
        self._build(model)

        system = model.calls[0][0].content

        assert "005930" in system
        assert "KOSPI" not in system  # returns에 없다

    def test_an_empty_answer_is_asked_once_more(self) -> None:
        """후보가 있는데 경로를 하나도 안 내면 한 번 다시 묻는다.

        교정에는 **무엇이 잘못됐는지**를 싣는다 — 사유 없는 교정을 받은 모델이 같은 답을
        다시 내는 것을 thesis 판 11에서 이미 봤다.
        """
        model = FakeModel([NO_PATHS, ONE_PATH, NO_LINKS])

        paths = self._build(model)

        assert len(paths) == 1
        # 답변 → 교정 답변 → 링커. 링커는 답이 나왔을 때만 붙는다.
        assert len(model.calls) == 3
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


class TestVerifyLinks:
    """링커 답 검증(설계 §11.4). **프롬프트에만 두지 않는다** — 코드가 검사한다."""

    def _answer(self, **overrides) -> generation.LinkPathAnswer:
        base = {
            "source_target_code": "US10Y",
            "source_target_kind": "quote",
            "source_sign": "down",
            "source_date": "2026-08-11",
            "channels": ["할인율"],
            "target_kind": "instrument",
            "target_code": "005930",
            "target_date": "2026-08-12",
            "sign": "up",
            "confidence": "endpoint_observed",
            "reasoning": "국채금리가 내린 뒤 주가가 올랐다",
            "evidence_refs": [],
        }
        return generation.LinkPathAnswer(**(base | overrides))

    def _prices(self) -> dict[str, tuple[domain.DailyClose, ...]]:
        def rows(*values: float) -> tuple[domain.DailyClose, ...]:
            return tuple(
                domain.DailyClose(business_date=date(2026, 8, 10 + offset), close=value)
                for offset, value in enumerate(values)
            )

        return {
            "US10Y": rows(4.699, 4.684, 4.682, 4.641, 4.696),
            "005930": rows(230000, 239500, 255500, 268000, 274500),
            "KOSPI": rows(6299.66, 6345.53, 6579.04, 6813.34, 6977.94),
            "SOX": rows(11993.9, 12098.5, 12399.4, 12456, 12417),
        }

    def _verify(self, answer, **overrides):
        kwargs = {
            "window": WINDOW,
            "answered": {"US10Y", "005930", "SOX", "KOSPI"},
            "target_codes": {"US10Y", "005930", "SOX", "KOSPI"},
            "prices": self._prices(),
            "found": _candidates(),
        }
        return generation.verify_links([answer], **(kwargs | overrides))

    def test_a_verified_link_keeps_its_source(self) -> None:
        kept, dropped = self._verify(self._answer())

        assert dropped == ()
        assert kept[0].source_target_code == "US10Y"
        assert kept[0].confidence == "endpoint_observed"

    def test_a_source_the_first_answer_never_reached_is_dropped(self) -> None:
        """**"이미 낸 경로의 대상일 때만"이 이 절의 조건 그 자체다**(설계 §11.4)."""
        kept, dropped = self._verify(self._answer(), answered={"005930"})

        assert kept == ()
        assert "앞 답의 대상이 아니다" in dropped[0]

    def test_a_path_back_to_itself_is_dropped(self) -> None:
        """사슬에는 시간 순서가 없어 모델이 자기 자신으로 돌아오는 경로를 만들 수 있다."""
        kept, dropped = self._verify(self._answer(target_code="US10Y", target_kind="quote"))

        assert kept == ()
        assert "자기 자신" in dropped[0]

    def test_a_cause_later_than_its_effect_is_dropped(self) -> None:
        kept, dropped = self._verify(
            self._answer(source_date="2026-08-13", target_date="2026-08-11")
        )

        assert kept == ()
        assert "늦다" in dropped[0]

    def test_an_overseas_cause_on_the_same_day_is_dropped(self) -> None:
        """**미국 종가는 KRX보다 늦게 정해진다**(설계 §11.5).

        프로토타입이 `SOX ↑ → KOSPI ↑`를 둘 다 8/12로 냈는데 시간 순서가 거꾸로다.
        종가 블록에 날짜만 있고 시각이 없어 모델은 이것을 못 가린다.
        """
        kept, dropped = self._verify(
            self._answer(
                source_target_code="SOX",
                source_sign="up",
                source_date="2026-08-12",
                target_code="KOSPI",
                target_kind="index",
                target_date="2026-08-12",
            )
        )

        assert kept == ()
        assert "같은 날" in dropped[0]

    def test_a_domestic_cause_on_the_same_day_survives(self) -> None:
        """국내가 원인이고 해외가 결과이면 같은 날이 정상이다. KRX가 먼저 닫는다."""
        kept, _ = self._verify(
            self._answer(
                source_target_code="005930",
                source_target_kind="instrument",
                source_sign="up",
                source_date="2026-08-12",
                target_code="SOX",
                target_kind="quote",
                target_date="2026-08-12",
                sign="up",
            )
        )

        assert len(kept) == 1

    def test_a_date_outside_the_week_is_dropped(self) -> None:
        kept, dropped = self._verify(self._answer(target_date="2026-08-20"))

        assert kept == ()
        assert "대상 주 밖" in dropped[0]

    def test_endpoint_observed_falls_back_when_the_closes_disagree(self) -> None:
        """**모델이 주장하고 코드가 확인한다.** 종가가 안 맞으면 경로를 버리지 않고 내린다 —
        연결 자체는 틀리지 않았고 "값이 그렇게 보였다"만 못 미더운 것이라, 버리면 관측을 잃는다."""
        kept, dropped = self._verify(self._answer(source_sign="up"))

        assert dropped == ()
        assert kept[0].confidence == "plausible"
        assert kept[0].source_sign == "up"

    def test_a_chain_longer_than_the_limit_is_dropped(self) -> None:
        kept, dropped = self._verify(
            self._answer(channels=[f"c{n}" for n in range(domain.MAX_CHAIN + 1)])
        )

        assert kept == ()
        assert "사슬" in dropped[0]

    def test_refs_outside_the_registry_are_dropped(self) -> None:
        kept, _ = self._verify(
            self._answer(evidence_refs=["document:84026", "document:99999"])
        )

        assert kept[0].evidence_refs == ("document:84026",)


class TestLinkBlocks:
    def test_the_price_block_shows_every_target(self) -> None:
        """**코드가 싣는다**(설계 §11.3). 모델이 툴을 안 부르면 링커가 볼 숫자가 없다."""
        block = generation.price_block(
            {
                "US10Y": (
                    domain.DailyClose(business_date=date(2026, 8, 10), close=4.699),
                    domain.DailyClose(business_date=date(2026, 8, 11), close=4.684),
                )
            }
        )

        assert "US10Y" in block
        assert "08/10 4.699" in block

    def test_the_answered_block_shows_channel_names_not_ids(self) -> None:
        """`market_channel`의 자연키가 이름이라 링커는 이름만 낸다. id를 보여 주면 못 잇는다."""
        path = generation.VerifiedPath(
            event=generation.NodeChoice(existing_id="e:12"),
            event_date="",
            channels=(
                generation.NodeChoice(existing_id="c:15"),
                generation.NodeChoice(new_name="밸류에이션"),
            ),
            target_kind="instrument",
            target_code="005930",
            sign="up",
            confidence="plausible",
            reasoning="",
            evidence_refs=(),
        )

        block = generation.answered_block((path,), {"c:15": "금리 기대"})

        assert "금리 기대 > 밸류에이션" in block
        assert "c:15" not in block


class TestTheLinkerRuns:
    def test_a_link_becomes_part_of_the_result(self) -> None:
        """`answer` 뒤에 `link`가 한 번 붙고, 그 결과가 쌍의 두 번째로 나온다."""
        model = FakeModel([ONE_PATH, ONE_LINK])
        prices = {
            "005930": (
                domain.DailyClose(business_date=date(2026, 8, 10), close=239500),
                domain.DailyClose(business_date=date(2026, 8, 11), close=230000),
            ),
            "KTB10Y": (
                domain.DailyClose(business_date=date(2026, 8, 11), close=4.20),
                domain.DailyClose(business_date=date(2026, 8, 12), close=4.27),
            ),
        }

        built = _build_with(generation.CausalBuilder(model), prices)
        paths, links = built.paths, built.links

        assert len(paths) == 1
        assert len(links) == 1
        assert links[0].source_target_code == "005930"
        assert links[0].confidence == "endpoint_observed"

    def test_an_empty_link_answer_is_not_repaired(self) -> None:
        """**링커 0건은 정상 답이다.** 다시 물으면 없는 것을 만든다(설계 §11.3)."""
        model = FakeModel([ONE_PATH, NO_LINKS])

        built = _build_with(generation.CausalBuilder(model))
        paths, links = built.paths, built.links

        assert len(paths) == 1
        assert links == ()
        assert len(model.calls) == 2


class TestTruncationIsMarked:
    """왕복 상한에서 끊긴 조사는 로그만 남겼다(G-37). `thesis/generation`처럼 상태에 남긴다."""

    def test_a_reply_that_still_wants_tools_at_the_cap_is_truncated(self) -> None:
        from langchain_core.messages import AIMessage

        reply = AIMessage("", tool_calls=[{"name": "price_window", "args": {}, "id": "call_1"}])
        state = {"messages": [reply], "tool_rounds": generation.MAX_TOOL_ROUNDS}

        assert generation.CausalBuilder._mark_truncation(state) == {"investigation_truncated": True}

    def test_a_reply_that_stopped_asking_is_not_truncated(self) -> None:
        from langchain_core.messages import AIMessage

        state = {"messages": [AIMessage("done")], "tool_rounds": generation.MAX_TOOL_ROUNDS}

        assert generation.CausalBuilder._mark_truncation(state) == {"investigation_truncated": False}

    def test_the_flag_travels_out_of_build(self) -> None:
        model = FakeModel([ONE_PATH, NO_LINKS])

        built = _build_with(generation.CausalBuilder(model))

        assert built.investigation_truncated is False
        assert built.attempts == 0

    def test_a_repaired_build_reports_its_attempt(self) -> None:
        model = FakeModel([NO_PATHS, NO_PATHS])

        built = _build_with(generation.CausalBuilder(model))

        assert built.paths == ()
        assert built.attempts == 1
