# KIS Overseas Index Daily Bars Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect official KIS daily OHLCV for `SP500` (`SPX`) and `NASDAQ` (`COMP`) into `index_daily`.

**Architecture:** Add a daily collector beside, not inside, the existing overseas closing-minute collector. Reuse `OverseasIndex`, shared KIS transport, `index_daily` storage, and the established domestic-index pagination strategy; add a separate US-calendar DAG because its backfill and failure boundary differ from the closing-minute DAG.

**Tech Stack:** Python 3.13, Apache Airflow 3.3, Pydantic, PostgreSQL, pytest

**Spec:** `docs/collection/kis-index-daily-collection.md` sections 1–3 and 6–12

## Global Constraints

- Use `/uapi/overseas-price/v1/quotations/inquire-daily-chartprice`, TR ID `FHKST03030100`, market `N`, period `D`.
- Reuse `OverseasIndex`; do not add symbols or quote-symbol migrations.
- Store `stck_bsop_date` directly as the New York business date; do not derive a date from UTC.
- Permit zero index volume, but reject invalid OHLC, duplicate dates, stale automatic runs, and truncated pagination.
- Keep this DAG independent from `kis_overseas_index_close` and Yahoo fallback logic.
- Add no dependency or schema change.

---

## File Map

- Create `airflow/modules/collectors/market/kis_overseas_index_daily.py`: daily response models, pagination, validation, and `index_daily` storage.
- Create `airflow/dags/kis_overseas_index_daily.py`: US calendar, date parameters, schedule, retry, and per-symbol transaction policy.
- Create `tests/collectors/test_kis_overseas_index_daily.py`: request, parsing, pagination, storage, and SQL shape.
- Create `tests/dags/test_kis_overseas_index_daily.py`: schedule, run-derived session date, parameters, windows, holiday behavior.
- Modify `docs/collection/kis-overseas-index-close.md`: replace the stale “daily path not used” statement with a link to the new collector after implementation.

### Task 1: Implement the overseas daily parser and fetch loop

**Files:**
- Create: `airflow/modules/collectors/market/kis_overseas_index_daily.py`
- Create: `tests/collectors/test_kis_overseas_index_daily.py`

**Interfaces:**
- Consumes: `OverseasIndex`, `send_get`, `result_error`, `KisPayloadError`.
- Produces:
  - `KisOverseasDailyRow(business_date, open, high, low, close, volume)`
  - `OverseasDailyBar(business_date, open, high, low, close, volume)`
  - `OverseasIndexDailyFetch(index, start_date, end_date, bars, page_count, started_at, completed_at)`
  - `KisOverseasIndexDailyCollector.fetch(index, start_date, end_date, *, sleep=0.5)`

- [ ] **Step 1: Create a minimal response builder and failing request test**

```python
def test_fetch_sends_the_official_overseas_daily_contract(monkeypatch):
    requests, send = daily_send([(daily_payload(("20260821", "20260820")), "")])
    monkeypatch.setattr(kis_overseas_index_daily, "send_get", send)

    fetch = collector().fetch(
        OverseasIndex.SP500,
        date(2026, 8, 20),
        date(2026, 8, 21),
        sleep=0,
    )

    path, tr_id, query, tr_cont = requests[0]
    assert path == "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
    assert tr_id == "FHKST03030100"
    assert tr_cont == ""
    assert query == {
        "FID_COND_MRKT_DIV_CODE": "N",
        "FID_INPUT_ISCD": "SPX",
        "FID_INPUT_DATE_1": "20260820",
        "FID_INPUT_DATE_2": "20260821",
        "FID_PERIOD_DIV_CODE": "D",
    }
    assert fetch.index is OverseasIndex.SP500
```

- [ ] **Step 2: Add failing parser and pagination tests**

Test `COMP`, reverse-to-ascending order, zero volume, `tr_cont` continuation, silent truncation window walking, empty final page, duplicate/out-of-range dates, bad JSON, `rt_cd`, missing `output2`, mismatched echoed code, non-positive/non-finite OHLC, inconsistent high/low, negative volume, and page-cap exhaustion.

```python
def test_daily_rows_keep_the_provider_business_date():
    parsed = parse_daily_rows(daily_payload(("20260821",)))
    assert parsed[0].business_date == date(2026, 8, 21)
```

- [ ] **Step 3: Run and confirm import failure**

```bash
uv run pytest tests/collectors/test_kis_overseas_index_daily.py -q
```

Expected: FAIL because the module does not exist.

- [ ] **Step 4: Implement models and validation**

```python
class KisOverseasDailyRow(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    business_date: str = Field(alias="stck_bsop_date")
    open: str = Field(alias="ovrs_nmix_oprc")
    high: str = Field(alias="ovrs_nmix_hgpr")
    low: str = Field(alias="ovrs_nmix_lwpr")
    close: str = Field(alias="ovrs_nmix_prpr")
    volume: str = Field(default="0", alias="acml_vol")
```

Use `DailyIndexBar`-equivalent positive finite OHLC and consistent-range validation, with volume `ge=0`. Parse `YYYYMMDD` directly to `date`.

- [ ] **Step 5: Implement fetch pagination**

Request the inclusive input range. Stop on an empty page; when response `tr_cont` is `M` or `F`, send the next request with `tr_cont="N"`; otherwise set the next end date to one day before the oldest returned date. Reject duplicate or out-of-range dates, an empty combined result, and more than ten pages. Return bars sorted ascending. Keep overseas constants local to the new module.

- [ ] **Step 6: Run tests and static checks**

```bash
uv run pytest tests/collectors/test_kis_overseas_index_daily.py -q
uv run ruff check airflow/modules/collectors/market/kis_overseas_index_daily.py tests/collectors/test_kis_overseas_index_daily.py
uv run pyrefly check airflow/modules/collectors/market/kis_overseas_index_daily.py
```

Expected: all commands exit 0.

### Task 2: Store overseas daily bars with lineage

**Files:**
- Modify: `airflow/modules/collectors/market/kis_overseas_index_daily.py`
- Modify: `tests/collectors/test_kis_overseas_index_daily.py`

**Interfaces:**
- Consumes: `read_sql`, `SOURCE_RECORD_INSERT`, `execute_upserts`.
- Produces: `KisOverseasIndexDailyCollector.store(connection, fetch) -> int`.

- [ ] **Step 1: Write the failing storage test**

```python
def test_store_writes_lineage_and_index_daily_rows(monkeypatch):
    fetch = fetched_sp500(monkeypatch)
    connection = FakeConnection()

    stored = collector().store(connection, fetch)

    assert stored == len(fetch.bars)
    source = connection.recorded_cursor.calls[0]
    assert source[1][1:3] == ("kis", "overseas_index_daily")
    metadata = json.loads(source[1][8])
    assert metadata["symbol"] == "SP500"
    assert metadata["kis_code"] == "SPX"
    assert metadata["page_count"] == fetch.page_count
```

Assert the upsert row order is `(kis, SP500, business_date, open, high, low, close, volume, source_record_id)` and its columns match `IndexDaily.__table__`.

- [ ] **Step 2: Run and confirm failure**

```bash
uv run pytest tests/collectors/test_kis_overseas_index_daily.py -q
```

Expected: FAIL because `store()` and/or `OVERSEAS_INDEX_DAILY_SOURCE_KEY` do not exist.

- [ ] **Step 3: Implement storage**

Set `OVERSEAS_INDEX_DAILY_SOURCE_KEY = "overseas_index_daily"` and load a module-local `INDEX_DAILY_UPSERT = read_sql("postgres", "index_daily", "upsert.sql")`. Write one source record per index/window with payload `None` and metadata keys `symbol`, `kis_code`, `start_date`, `end_date`, `page_count`, `bar_count`, `earliest_date`, and `latest_date`; then batch through `execute_upserts()`.

- [ ] **Step 4: Run the collector test file**

```bash
uv run pytest tests/collectors/test_kis_overseas_index_daily.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the collector**

```bash
git add airflow/modules/collectors/market/kis_overseas_index_daily.py tests/collectors/test_kis_overseas_index_daily.py
git commit -m "feat(kis): collect overseas index daily bars"
```

### Task 3: Add the US-calendar Airflow DAG

**Files:**
- Create: `airflow/dags/kis_overseas_index_daily.py`
- Create: `tests/dags/test_kis_overseas_index_daily.py`

**Interfaces:**
- Consumes: `KisOverseasIndexDailyCollector`, `OverseasIndex`, `us_session_date`, `us_equity_open_day`.
- Produces: DAG `kis_overseas_index_daily`, schedule `35 7 * * 2-6`, `start_date`/`end_date` parameters, 200-day `fetch_windows()`.

- [ ] **Step 1: Write failing DAG tests**

```python
def test_the_daily_dag_runs_after_close_collection_and_before_briefing():
    dag = kis_overseas_index_daily.kis_overseas_index_daily
    assert dag.schedule == "35 7 * * 2-6"
    assert dag.max_active_runs == 1


def test_the_session_date_comes_from_the_run(monkeypatch):
    run_after = pendulum.datetime(2026, 8, 22, 7, 35, tz="Asia/Seoul")
    monkeypatch.setattr(
        kis_overseas_index_daily,
        "get_current_context",
        lambda: {"data_interval_end": run_after},
    )
    assert kis_overseas_index_daily._session_date() == date(2026, 8, 21)
```

Also assert these exact contracts: a non-ISO parameter raises `ValueError`; `start_date > end_date` raises `ValueError`; a long range is split into ascending, non-overlapping inclusive windows of at most 200 calendar days; and the task skips only when `us_equity_open_day()` returns literal `False`.

- [ ] **Step 2: Run and confirm import failure**

```bash
uv run pytest tests/dags/test_kis_overseas_index_daily.py -q
```

Expected: FAIL because the DAG does not exist.

- [ ] **Step 3: Implement the DAG**

Parse optional `start_date` and `end_date` as ISO dates; default the end to the New York session date derived from `data_interval_end` and the start to 200 calendar days earlier. Split an explicit longer range into ascending, non-overlapping inclusive windows of at most 200 days. Skip only when an automatic run's `us_equity_open_day()` result is literal `False`. Iterate both `OverseasIndex` values with one shared token and DB connection, one transaction per index/window, and one credential/token retry. Keep successfully completed indexes and raise one aggregate error after attempting both.

- [ ] **Step 4: Run DAG and collector tests**

```bash
uv run pytest tests/dags/test_kis_overseas_index_daily.py tests/collectors/test_kis_overseas_index_daily.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the DAG**

```bash
git add airflow/dags/kis_overseas_index_daily.py tests/dags/test_kis_overseas_index_daily.py
git commit -m "feat(kis): schedule overseas index daily bars"
```

### Task 4: Update documentation and integration coverage

**Files:**
- Modify: `docs/collection/kis-overseas-index-close.md`
- Modify: `tests/dags/test_quote_intraday.py`

- [ ] **Step 1: Replace the stale daily-path statement**

Change “일봉 API도 되지만 브리핑이 안 읽는 경로라 쓰지 않는다” to state that `kis_overseas_index_daily` stores the same official close in `index_daily` for history while the existing minute path remains the briefing source.

- [ ] **Step 2: Assert the close and daily schedules do not collide**

```python
def test_the_us_close_and_daily_dags_run_in_order():
    assert kis_overseas_index_close.SCHEDULE == "30 7 * * 2-6"
    assert kis_overseas_index_daily.kis_overseas_index_daily.schedule == "35 7 * * 2-6"
```

- [ ] **Step 3: Run integration tests**

```bash
uv run pytest tests/dags/test_quote_intraday.py tests/dags/test_kis_overseas_index_close.py tests/dags/test_kis_overseas_index_daily.py tests/collectors/test_kis_overseas_index.py tests/collectors/test_kis_overseas_index_daily.py tests/modules/test_technical.py -q
```

Expected: PASS.

- [ ] **Step 4: Run repository checks**

```bash
uv run ruff check airflow apps migrations tests
uv run pyrefly check airflow apps
```

Expected: both commands exit 0.

- [ ] **Step 5: Commit docs and integration coverage**

```bash
git add docs/collection/kis-overseas-index-close.md tests/dags/test_quote_intraday.py
git commit -m "docs(kis): connect overseas daily history"
```

### Task 5: Refresh the graph

- [ ] **Step 1: Update graphify**

```bash
graphify update .
```

Expected: the new daily collector and DAG connect to `OverseasIndex`, `index_daily`, and their tests without extraction errors.

- [ ] **Step 2: Commit graph output only if changed**

```bash
git add graphify-out
git commit -m "docs(graph): add overseas index daily flow"
```

Skip when `git status --short graphify-out` is empty.
