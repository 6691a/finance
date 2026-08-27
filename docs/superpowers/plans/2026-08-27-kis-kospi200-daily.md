# KOSPI200 Daily Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `KOSPI200` to the existing KIS domestic-index daily collection without changing its API, schedule, storage, or failure behavior.

**Architecture:** Keep `KisIndexDailyCollector` unchanged because it already accepts every `DomesticIndex`. Replace the market-movement subset accidentally used by the DAG with an explicit daily target tuple containing the full enum, and lock that coverage with tests.

**Tech Stack:** Python 3.13, Apache Airflow 3.3, Pydantic, pytest, PostgreSQL

**Spec:** `docs/collection/kis-index-daily-collection.md` sections 1, 2, 5, 7–10

## Global Constraints

- Reuse `/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice` and TR ID `FHKUP03500100`.
- Keep `MOVEMENT_INDEXES` limited to KOSPI and KOSDAQ; it belongs only to the market-distribution request.
- Do not add a table, migration, collector, dependency, or DAG.
- Keep the existing 18:20 KST schedule, 200-calendar-day windows, retries, transaction boundary, and backfill parameters.
- Use TDD and commit only the files listed in each task.

---

## File Map

- Modify `airflow/dags/kis_index_daily.py`: define and iterate the complete daily-index target tuple; update user-facing copy.
- Modify `tests/dags/test_kis_index_daily.py`: assert the DAG target contract includes all `DomesticIndex` members.
- Modify `tests/collectors/test_kis_index_daily_collector.py`: verify the existing collector sends code `2001` and preserves symbol `KOSPI200`.

### Task 1: Lock the missing target with failing tests

**Files:**
- Modify: `tests/dags/test_kis_index_daily.py`
- Modify: `tests/collectors/test_kis_index_daily_collector.py`

**Interfaces:**
- Consumes: `DomesticIndex`, `KisIndexDailyCollector.fetch()`
- Produces: required module constant `DAILY_INDEXES: tuple[DomesticIndex, ...]`

- [ ] **Step 1: Add the DAG coverage test**

```python
from modules.collectors.kis import DomesticIndex


def test_every_domestic_index_is_a_daily_target():
    assert kis_index_daily.DAILY_INDEXES == tuple(DomesticIndex)
    assert {index.value for index in kis_index_daily.DAILY_INDEXES} == {
        "KOSPI",
        "KOSPI200",
        "KOSDAQ",
    }
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

Run:

```bash
uv run pytest tests/dags/test_kis_index_daily.py tests/collectors/test_kis_index_daily_collector.py -q
```

Expected: the DAG test fails because `DAILY_INDEXES` does not exist; existing collector tests and the new `KOSPI200` request test pass.

### Task 2: Switch the DAG to the complete target set

**Files:**
- Modify: `airflow/dags/kis_index_daily.py`
- Test: `tests/dags/test_kis_index_daily.py`
- Test: `tests/collectors/test_kis_index_daily_collector.py`

**Interfaces:**
- Produces: `DAILY_INDEXES = tuple(DomesticIndex)`
- Preserves: `KisIndexDailyCollector.fetch(index, start_date, end_date, *, sleep=...)`

- [ ] **Step 1: Replace the movement-subset import and define the daily targets**

```python
from modules.collectors.kis import (
    DomesticIndex,
    KisHTTPError,
    KisPayloadError,
    KisResultError,
    KisTimeWindowError,
    access_token,
)

DAILY_INDEXES: tuple[DomesticIndex, ...] = tuple(DomesticIndex)
```

Delete `from modules.collectors.market.kis_quote import MOVEMENT_INDEXES`.

- [ ] **Step 2: Use the new tuple in the existing loop**

```python
for index in DAILY_INDEXES:
    for window_start, window_end in windows:
        ...
```

Do not alter the inner fetch, exception, transaction, logging, or failure aggregation code.

- [ ] **Step 3: Update only stale user-facing copy**

Change the module title and DAG description from `KOSPI·KOSDAQ` to `KOSPI·KOSPI200·KOSDAQ`. Do not rewrite the rest of the runbook.

- [ ] **Step 4: Run the focused tests**

```bash
uv run pytest tests/dags/test_kis_index_daily.py tests/collectors/test_kis_index_daily_collector.py -q
```

Expected: PASS.

- [ ] **Step 5: Run style and type checks for touched Python files**

```bash
uv run ruff check airflow/dags/kis_index_daily.py tests/dags/test_kis_index_daily.py tests/collectors/test_kis_index_daily_collector.py
uv run pyrefly check airflow/dags/kis_index_daily.py
```

Expected: both commands exit 0.

- [ ] **Step 6: Commit the independently deployable fix**

```bash
git add airflow/dags/kis_index_daily.py tests/dags/test_kis_index_daily.py tests/collectors/test_kis_index_daily_collector.py
git commit -m "fix(kis): collect KOSPI200 daily bars"
```

### Task 3: Verify integration and refresh the graph

**Files:**
- Modify: `graphify-out/*` through the generated update only

- [ ] **Step 1: Run the relevant regression set**

```bash
uv run pytest tests/dags/test_kis_index_daily.py tests/collectors/test_kis_index_daily_collector.py tests/modules/test_technical.py tests/dags/test_technical_signal_daily.py -q
```

Expected: PASS with zero failures.

- [ ] **Step 2: Update the knowledge graph**

```bash
graphify update .
```

Expected: the graph contains the new `DAILY_INDEXES` relationship and reports no extraction error.

- [ ] **Step 3: Commit generated graph changes if the project hook did not already commit them**

```bash
git add graphify-out
git commit -m "docs(graph): refresh KIS daily coverage"
```

Skip this commit when `git status --short graphify-out` is empty.

