# KOSPI200 Daily Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

- 상태: **구현 완료(2026-08-27, 커밋 `5a19d9f`).** 아래 체크박스는 계획 시점 그대로 둔다.
  `airflow/dags/kis_index_daily.py:213`이 `DomesticIndex`를 순회한다.

**Goal:** Add `KOSPI200` to the existing KIS domestic-index daily collection without changing its API, schedule, storage, or failure behavior.

**Architecture:** `KisIndexDailyCollector` already accepts every `DomesticIndex`. The DAG accidentally iterates the market-movement subset, so the fix is to iterate the enum itself. This is one line of production code and the task list stays that size.

**Tech Stack:** Python 3.13, Apache Airflow 3.3, Pydantic, pytest, PostgreSQL

**Spec:** `docs/collection/kis-index-daily-collection.md` sections 1, 2, 5, 7–10

## Global Constraints

- Reuse `/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice` and TR ID `FHKUP03500100`.
- Keep `MOVEMENT_INDEXES` limited to KOSPI and KOSDAQ; it belongs only to the market-distribution request in `kis_quote_intraday`.
- Do not add a table, migration, collector, dependency, or DAG.
- **Do not introduce a named target constant.** `DAILY_INDEXES = tuple(DomesticIndex)` can only be tested against `tuple(DomesticIndex)`, which asserts nothing. Iterate `DomesticIndex` directly and guard the actual regression: the DAG must not import the movement subset again.
- **KOSPI200 bars do not become technical signals.** `modules/technical/signals.py` keeps an explicit whitelist (`SIGNAL_INDEXES = ("KOSPI", "KOSDAQ")`). Widening it is a separate decision outside this plan. Until then the new bars are read by `quote_daily` queries and the `daily_history` thesis tool only.
- Keep the existing 18:20 KST schedule, 200-calendar-day windows, retries, transaction boundary, and backfill parameters.
- Use TDD and commit only the files listed in each task.

---

## File Map

- Modify `airflow/dags/kis_index_daily.py`: iterate `DomesticIndex`; update user-facing copy.
- Modify `tests/dags/test_kis_index_daily.py`: guard against the movement subset returning.
- Modify `tests/collectors/test_kis_index_daily_collector.py`: verify the existing collector sends code `2001` and preserves symbol `KOSPI200`.

### Task 1: Lock the missing coverage with failing tests

**Files:**
- Modify: `tests/dags/test_kis_index_daily.py`
- Modify: `tests/collectors/test_kis_index_daily_collector.py`

**Interfaces:**
- Consumes: `DomesticIndex`, `KisIndexDailyCollector.fetch()`

- [ ] **Step 1: Add the DAG coverage guard**

```python
def test_the_daily_dag_does_not_reuse_the_market_movement_subset():
    # 상승·보합·하락 분포용 부분집합이다. 일봉 순회에 쓰면 KOSPI200이 조용히 빠진다.
    assert not hasattr(kis_index_daily, "MOVEMENT_INDEXES")
```

- [ ] **Step 2: Add the collector contract test beside the existing KOSDAQ test**

```python
def test_kospi200_uses_its_own_index_code(self, monkeypatch):
    requests, send = daily_send([(index_daily_payload(("20260821",)), "")])
    monkeypatch.setattr(kis_index_daily, "send_get", send)

    fetch = daily_collector().fetch(
        DomesticIndex.KOSPI200,
        DAILY_SPAN_START,
        DAILY_SPAN_END,
        sleep=0,
    )

    assert requests[0][2]["FID_INPUT_ISCD"] == "2001"
    assert fetch.symbol == "KOSPI200"
```

- [ ] **Step 3: Run the focused tests and confirm the intended failure**

```bash
uv run pytest tests/dags/test_kis_index_daily.py tests/collectors/test_kis_index_daily_collector.py -q
```

Expected: the DAG guard fails because the module still imports `MOVEMENT_INDEXES`; the new `KOSPI200` request test passes because the collector already accepts every member.

### Task 2: Switch the DAG to the whole enum

**Files:**
- Modify: `airflow/dags/kis_index_daily.py`
- Test: `tests/dags/test_kis_index_daily.py`
- Test: `tests/collectors/test_kis_index_daily_collector.py`

**Interfaces:**
- Preserves: `KisIndexDailyCollector.fetch(index, start_date, end_date, *, sleep=...)`

- [ ] **Step 1: Replace the movement-subset import**

Add `DomesticIndex` to the existing `modules.collectors.kis` import block and delete `from modules.collectors.market.kis_quote import MOVEMENT_INDEXES`.

- [ ] **Step 2: Iterate the enum in the existing loop**

```python
for index in DomesticIndex:
    for window_start, window_end in windows:
        ...
```

Do not alter the inner fetch, exception, transaction, logging, or failure aggregation code.

- [ ] **Step 3: Update only stale user-facing copy**

Change the module title and DAG description from `KOSPI·KOSDAQ` to `KOSPI·KOSPI200·KOSDAQ`. Add one line to the module docstring stating that the new symbol feeds `quote_daily` reads and the `daily_history` tool, not `technical_signal_daily`, whose target list is separate. Do not rewrite the rest of the runbook.

- [ ] **Step 4: Run the focused tests and the static checks**

```bash
uv run pytest tests/dags/test_kis_index_daily.py tests/collectors/test_kis_index_daily_collector.py tests/dags/test_technical_signal_daily.py -q
uv run ruff check airflow/dags/kis_index_daily.py tests/dags/test_kis_index_daily.py tests/collectors/test_kis_index_daily_collector.py
uv run pyrefly check airflow/dags/kis_index_daily.py
```

Expected: all commands exit 0.

- [ ] **Step 5: Commit the fix and refresh the graph**

```bash
git add airflow/dags/kis_index_daily.py tests/dags/test_kis_index_daily.py tests/collectors/test_kis_index_daily_collector.py
git commit -m "fix(kis): collect KOSPI200 daily bars"
graphify update .
git add graphify-out && git commit -m "docs(graph): refresh KIS daily coverage"
```

Skip the second commit when `git status --short graphify-out` is empty.
