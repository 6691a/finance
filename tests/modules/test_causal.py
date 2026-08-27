"""주간 인과 그래프의 순수 함수.

계약은 docs/analysis/market-causal-graph.md다. 이 파일은 LLM도 DB도 부르지 않는다.
"""

from datetime import UTC, date, datetime
from typing import Any, Self

import pytest

from modules.causal import candidates, domain


class FakeCursor:
    """PEP 249 커서 흉내. 쿼리 종류를 안 가리고 준비된 행을 그대로 준다."""

    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows
        self.calls: list[tuple[str, Any]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: str, parameters: Any = ()) -> None:
        self.calls.append((statement, parameters))

    def fetchall(self) -> list[tuple]:
        return self._rows


class FakeConnection:
    def __init__(self, rows: list[tuple] | None = None) -> None:
        self._rows = rows or []
        self.cursors: list[FakeCursor] = []

    def cursor(self) -> FakeCursor:
        cursor = FakeCursor(list(self._rows))
        self.cursors.append(cursor)
        return cursor


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
