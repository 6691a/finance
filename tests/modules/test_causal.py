"""주간 인과 그래프의 순수 함수.

계약은 docs/analysis/market-causal-graph.md다. 이 파일은 LLM도 DB도 부르지 않는다.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any, Self

import pytest

from modules.causal import candidates, domain


class FakeCursor:
    """PEP 249 커서 흉내.

    `results`가 있으면 SQL 안의 조각(`FROM index_daily` 등)으로 결과를 고른다. 실제 SQL을
    돌리지 않으므로 컬럼 이름과 조인은 검증하지 못한다 — 그것은 운영 DB에 읽기 전용으로
    한 번 돌려 보는 것이 맡는다(설계 §10.3).
    """

    def __init__(self, connection: "FakeConnection") -> None:
        self._connection = connection
        self._rows: list[tuple] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: Any = ()) -> None:
        self._connection.calls.append((statement, parameters))
        if self._connection.results:
            self._rows = next(
                (rows for key, rows in self._connection.results.items() if key in statement),
                [],
            )
        else:
            self._rows = list(self._connection.rows)

    def fetchall(self) -> list[tuple]:
        return list(self._rows)


class FakeConnection:
    def __init__(
        self,
        rows: list[tuple] | None = None,
        results: dict[str, list[tuple]] | None = None,
    ) -> None:
        self.rows = rows or []
        self.results = results or {}
        self.calls: list[tuple[str, Any]] = []

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)


class TestResolveWeek:
    """대상 주 `W`를 정하는 규칙. 설계 §2."""

    def test_run_monday_resolves_to_the_monday_two_weeks_earlier(self) -> None:
        """`W+2` 월요일 07:00 KST 실행이 `W` 월요일을 낸다.

        스케줄이 UTC 일 22:00이라 `logical_date`의 UTC 날짜는 일요일이다. KST로 바꾸지
        않고 계산하면 한 주가 밀린다.
        """
        logical = datetime(2026, 8, 23, 22, 0, tzinfo=UTC)  # KST 2026-08-24(월) 07:00

        assert domain.resolve_week(logical, None) == date(2026, 8, 10)

    def test_param_wins_over_the_logical_date(self) -> None:
        """수동 재실행은 벽시계가 아니라 Param이 정한다."""
        logical = datetime(2026, 8, 23, 22, 0, tzinfo=UTC)

        assert domain.resolve_week(logical, "2026-07-06") == date(2026, 7, 6)

    def test_param_that_is_not_a_monday_is_rejected(self) -> None:
        """`week_start`가 자연키의 축이라 화요일을 받으면 키가 어긋난다."""
        logical = datetime(2026, 8, 23, 22, 0, tzinfo=UTC)

        with pytest.raises(ValueError, match="Monday"):
            domain.resolve_week(logical, "2026-07-07")  # 화요일

    def test_iso_week_notation_is_rejected(self) -> None:
        """`date.fromisoformat`은 `2026-W28`도 받아 그 주의 월요일로 바꾼다.

        조용히 통과하면 어느 표기로 준 실행인지 나중에 못 가른다. 달력 하루만 받는다.
        """
        logical = datetime(2026, 8, 23, 22, 0, tzinfo=UTC)

        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            domain.resolve_week(logical, "2026-W28")


class TestWindow:
    """대상 주에서 조회 창을 뽑는다. 설계 §2."""

    def test_window_covers_the_target_week_and_the_reaction_week(self) -> None:
        week_start = date(2026, 8, 10)

        window = domain.window_for(week_start)

        assert window.week_start == date(2026, 8, 10)
        assert window.week_end == date(2026, 8, 14)  # W 금요일
        assert window.reaction_end == date(2026, 8, 21)  # W+1 금요일

    def test_as_of_at_is_the_reaction_friday_kst_1540_in_utc(self) -> None:
        """cutoff는 KST 15:40이고 저장·비교는 UTC다.

        KST 15:40 = UTC 06:40. 이 값을 KST로 두면 조회가 아홉 시간을 더 본다.
        """
        window = domain.window_for(date(2026, 8, 10))

        assert window.as_of_at == datetime(2026, 8, 21, 6, 40, tzinfo=UTC)


class TestInputHash:
    """무엇으로 만들었는지를 남기는 감사 값. 설계 §5.4."""

    def test_candidate_order_does_not_change_the_hash(self) -> None:
        """후보 조립 SQL의 반환 순서가 바뀌어도 같은 입력이면 같은 해시여야 한다."""
        first = domain.input_hash(
            week_start=date(2026, 8, 10),
            target_codes=["KOSPI", "005930"],
            candidate_refs=["document:2", "macro_change:USDKRW", "document:1"],
        )
        second = domain.input_hash(
            week_start=date(2026, 8, 10),
            target_codes=["005930", "KOSPI"],
            candidate_refs=["document:1", "document:2", "macro_change:USDKRW"],
        )

        assert first == second

    def test_a_different_candidate_set_changes_the_hash(self) -> None:
        base = domain.input_hash(
            week_start=date(2026, 8, 10),
            target_codes=["KOSPI"],
            candidate_refs=["document:1"],
        )
        added = domain.input_hash(
            week_start=date(2026, 8, 10),
            target_codes=["KOSPI"],
            candidate_refs=["document:1", "document:2"],
        )

        assert base != added

    def test_prompt_version_is_part_of_the_hash(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """문장이 바뀌면 같은 후보라도 다른 실행이다."""
        args = {
            "week_start": date(2026, 8, 10),
            "target_codes": ["KOSPI"],
            "candidate_refs": ["document:1"],
        }
        before = domain.input_hash(**args)
        monkeypatch.setattr(domain, "PROMPT_VERSION", "999")

        assert domain.input_hash(**args) != before


class TestResolveTargets:
    """대상 아홉. 종목만 마스터에서 읽고 나머지는 코드 상수다(설계 §0)."""

    def test_watched_stocks_join_the_fixed_targets(self) -> None:
        connection = FakeConnection(rows=[("000660",), ("005930",)])  # SQL은 ticker 순이다

        targets = candidates.resolve_targets(connection)

        assert [target.code for target in targets] == [
            "KOSPI",
            "KOSDAQ",
            "000660",
            "005930",
            "USDKRW",
            "US10Y",
            "SOX",
            "VIX",
            "NASDAQ100_FUT",
            "KRBASE",
            "KTB10Y",
        ]

    def test_each_target_declares_which_master_validates_it(self) -> None:
        """`target_kind`는 값의 성격이 아니라 저장소를 가른다(설계 §3.2.1)."""
        connection = FakeConnection(rows=[("005930",)])

        by_code = {target.code: target.kind for target in candidates.resolve_targets(connection)}

        assert by_code["KOSPI"] == "index"
        assert by_code["005930"] == "instrument"
        assert by_code["US10Y"] == "quote"
        assert by_code["KTB10Y"] == "indicator"

    def test_watched_stocks_grow_the_target_list(self) -> None:
        """관심종목을 늘리면 대상이 따라 는다 — 종목 코드를 코드에 계속 더하지 않는다."""
        connection = FakeConnection(rows=[("000660",), ("005930",), ("373220",)])

        targets = candidates.resolve_targets(connection)

        assert "373220" in [target.code for target in targets]
        assert len(targets) == 12

    def test_indicator_targets_carry_their_provider(self) -> None:
        """`indicator_observation`은 (provider, series_id)가 키다. series_id 하나로 걸면
        제공처가 늘어날 때 조용히 틀린다(저장소 규칙)."""
        connection = FakeConnection(rows=[("005930",)])

        by_code = {target.code: target for target in candidates.resolve_targets(connection)}

        assert by_code["KTB10Y"].provider == "ecos"
        assert by_code["KRBASE"].provider == "ecos"
        # 나머지 종류는 제공처를 자기 마스터가 안다.
        assert by_code["KOSPI"].provider is None


class TestFetchReturns:
    """실현 등락. SQL이 계산하고 모델은 만들지 않는다(설계 §0·§6)."""

    WINDOW = domain.window_for(date(2026, 8, 10))

    def _targets(self) -> tuple[domain.CausalTarget, ...]:
        return candidates.resolve_targets(
            FakeConnection(results={"FROM instrument": [("005930",)]})
        )

    def test_each_target_kind_reads_its_own_table(self) -> None:
        connection = FakeConnection(
            results={
                "FROM index_daily": [("KOSPI", 10.7669, -1.5493, -4.0267)],
                "FROM stock_investor_trade_daily": [("005930", 19.3478, -2.1858, -6.3752)],
                "FROM quote_daily": [("USDKRW", 0.7001, -0.0261, -1.9374)],
                "FROM indicator_observation": [("KTB10Y", 7.4, 6.9, 2.2)],
            }
        )

        returns = candidates.fetch_returns(connection, self._targets(), self.WINDOW)

        assert returns["KOSPI"].week == 10.7669
        assert returns["005930"].week == 19.3478
        assert returns["USDKRW"].week == 0.7001
        assert returns["KTB10Y"].week == 7.4

    def test_interest_rates_are_measured_in_basis_points(self) -> None:
        """가격 %와 금리 bp를 한 칸에 담을 수 없다. 단위를 값과 함께 들고 간다."""
        connection = FakeConnection(
            results={
                "FROM index_daily": [("KOSPI", 10.7669, -1.5493, -4.0267)],
                "FROM indicator_observation": [("KTB10Y", 7.4, 6.9, 2.2)],
            }
        )

        returns = candidates.fetch_returns(connection, self._targets(), self.WINDOW)

        assert returns["KOSPI"].unit == "percent"
        assert returns["KTB10Y"].unit == "basis_point"

    def test_a_target_with_a_missing_horizon_is_dropped(self) -> None:
        """값이 하나라도 없으면 그 대상을 저장하지 않는다 — NULL로 두면 "안 쟀다"와
        "잴 수 없었다"가 구분되지 않는다(설계 §6).

        반응 주가 아직 안 끝났거나 그 계열의 수집이 늦게 시작된 주가 그렇다.
        """
        connection = FakeConnection(
            results={
                "FROM index_daily": [
                    ("KOSPI", 1.66, None, None),  # T+1·T+5가 아직 없다
                    ("KOSDAQ", 1.19, -3.52, -5.93),
                ],
            }
        )

        returns = candidates.fetch_returns(connection, self._targets(), self.WINDOW)

        assert "KOSPI" not in returns
        assert "KOSDAQ" in returns

    def test_targets_with_no_row_at_all_are_absent(self) -> None:
        """수집이 시작되기 전 주는 행 자체가 없다. 그것도 조용히 빠진다."""
        connection = FakeConnection(results={"FROM index_daily": [("KOSPI", 1.0, 1.0, 1.0)]})

        returns = candidates.fetch_returns(connection, self._targets(), self.WINDOW)

        assert set(returns) == {"KOSPI"}


class TestFetchCandidates:
    """후보 조립. 코드가 먼저 좁히고 모델이 툴로 더 판다(설계 §5.1)."""

    WINDOW = domain.window_for(date(2026, 8, 10))

    def _targets(self) -> tuple[domain.CausalTarget, ...]:
        return candidates.resolve_targets(
            FakeConnection(results={"FROM instrument": [("005930",)]})
        )

    def _connection(self) -> FakeConnection:
        return FakeConnection(
            results={
                "FROM document": [
                    (
                        84026,
                        "삼성전자 반도체 수출 급증",
                        "요약",
                        "yonhap",
                        datetime(2026, 8, 14, 1, 0, tzinfo=UTC),
                        8,
                        "up",
                        ["005930", "KOSPI"],
                    ),
                    (
                        84100,
                        "미국 7월 소비자물가 둔화",
                        "요약",
                        "cnbc",
                        datetime(2026, 8, 12, 22, 0, tzinfo=UTC),
                        8,
                        "up",
                        ["CPI_M"],  # 대상 목록 밖 태그뿐이다
                    ),
                ],
                "FROM disclosure_event": [
                    (
                        "005930",
                        "20260819000123",
                        "삼성전자",
                        "자기주식취득결정",
                        date(2026, 8, 19),
                    )
                ],
                "FROM technical_signal": [(12, "KOSPI", date(2026, 8, 12), "golden_cross", "up")],
            }
        )

    def test_every_candidate_carries_a_ref(self) -> None:
        """모델이 인용한 근거만 저장하고 목록 밖 ref는 버린다. 그 목록이 여기서 만들어진다."""
        found = candidates.fetch_candidates(self._connection(), self._targets(), self.WINDOW)

        assert found.refs == (
            "disclosure:20260819000123",
            "document:84026",
            "document:84100",
            "technical_signal:12",
        )

    def test_documents_carry_every_tag_they_have(self) -> None:
        """태그는 표시용이다. 어느 대상에 붙었는지를 모델이 알아야 사건을 정확히 만든다."""
        found = candidates.fetch_candidates(self._connection(), self._targets(), self.WINDOW)

        assert found.documents[0].tags == ("005930", "KOSPI")
        assert found.documents[0].value_score == 8

    def test_a_document_tagged_only_outside_the_targets_is_still_a_candidate(self) -> None:
        """**대상 코드로 좁히지 않는다.** 좁히면 `CPI_M`에만 태그된 미국 물가 기사가 통째로
        빠지는데, 모델은 그 사건을 경로의 출발점으로 쓴다 — 2026-08-28 운영 실행에서 경로
        14개 중 8개가 그 사건이었고 근거는 우연히 딸려 온 것뿐이었다."""
        found = candidates.fetch_candidates(self._connection(), self._targets(), self.WINDOW)

        assert "document:84100" in found.refs

    def test_refs_are_sorted_so_the_input_hash_is_stable(self) -> None:
        """`input_hash`가 후보 ref를 접는다. 조회 순서가 흔들려도 같은 입력이면 같은 해시여야
        한다 — 여기서 이미 정렬해 두면 그 성질이 조립 단계에서 깨지지 않는다."""
        found = candidates.fetch_candidates(self._connection(), self._targets(), self.WINDOW)

        assert list(found.refs) == sorted(found.refs)

    def test_an_empty_week_yields_no_refs(self) -> None:
        """후보가 0건인 주도 정상이다. 7/06 주가 실제로 평가된 문서 0건이었다."""
        found = candidates.fetch_candidates(FakeConnection(), self._targets(), self.WINDOW)

        assert found.refs == ()


class TestVocabularyOptions:
    """다음 주 프롬프트에 실릴 어휘 후보. 이것이 주를 잇는다(설계 §4)."""

    WINDOW = domain.window_for(date(2026, 8, 17))

    def test_events_are_narrowed_by_date_and_channels_are_not(self) -> None:
        """사건은 수렴하지 않아 날짜로 좁히고, 경로는 수렴하므로 전부 준다."""
        connection = FakeConnection(
            results={
                "FROM market_event": [(812, "한은 기준금리 인상", date(2026, 8, 12))],
                "FROM market_channel": [(1, "할인율"), (2, "위험선호")],
            }
        )

        events, channels = candidates.fetch_vocabulary(connection, self.WINDOW)

        assert [option.node_id for option in events] == ["e:812"]
        assert [option.node_id for option in channels] == ["c:1", "c:2"]

    def test_the_event_lookback_is_passed_to_the_query(self) -> None:
        """몇 주를 거슬러 보는지는 코드 상수가 정한다. SQL에 숫자를 적으면 어긋난다."""
        connection = FakeConnection(results={"FROM market_event": []})

        candidates.fetch_vocabulary(connection, self.WINDOW)

        call = next(c for c in connection.calls if "FROM market_event" in c[0])
        assert call[1]["since"] == self.WINDOW.week_start - timedelta(
            weeks=domain.EVENT_LOOKBACK_WEEKS
        )

    def test_node_ids_are_prefixed_so_the_model_can_tell_them_apart(self) -> None:
        """`e:`와 `c:`가 없으면 모델이 사건 id를 경로 칸에 넣는다."""
        connection = FakeConnection(
            results={
                "FROM market_event": [(1, "사건", date(2026, 8, 12))],
                "FROM market_channel": [(1, "경로")],
            }
        )

        events, channels = candidates.fetch_vocabulary(connection, self.WINDOW)

        assert events[0].node_id == "e:1"
        assert channels[0].node_id == "c:1"
