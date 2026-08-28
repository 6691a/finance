# KIS Domestic Future Daily Bars Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

- 상태: **구현 완료(2026-08-27, 커밋 `de5e752`·`d146e5f`).** 아래 체크박스는 계획 시점 그대로 둔다.
  `airflow/dags/kis_future_daily.py`, `airflow/modules/collectors/market/kis_future_daily.py`,
  리비전 `f8a2c6d9e104`(`index_future_daily.contract_code`).

**Goal:** Collect official daily OHLCV for `KOSPI200_FUT` and `KOSDAQ150_FUT` while preserving the actual KIS contract code on every row.

**Architecture:** Add one focused collector and one Airflow DAG around KIS `inquire-daily-fuopchartprice`. Extend only `index_future_daily` with a nullable `contract_code`; Yahoo continuous futures continue writing `NULL`. The 2026-08-27 probe settled the code question — the intraday `A01609` form works on the daily API and the official example's `101W09` does not — so what is left to build is the enumeration of past quarterly contracts across a backfill range.

**Tech Stack:** Python 3.13, Apache Airflow 3.3, Pydantic, PostgreSQL, Alembic, pytest

**Spec:** `docs/collection/kis-index-daily-collection.md` sections 1–4 and 7–12

## Global Constraints

- The API contract is measured, not assumed. Section 4.4 of the spec is the source; do not re-probe and do not contradict it from the official examples, which are wrong about the code format.
- Use `/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice`, TR ID `FHKIF03020100`, market `F`, period `D`.
- Store the logical symbols `KOSPI200_FUT` and `KOSDAQ150_FUT`; store the exact queried short code in `contract_code`.
- The front contract includes expiry day and rolls on the following trading day. Do not volume-roll or price-adjust.
- **A 200-day window crosses at least one expiry, so `front_contract()` is not enough.** It answers only "which contract trades today". This plan needs a new function that enumerates the contracts covering a past date range. Task 1 fixes its code format; Task 3 writes it.
- **Future daily bars do not become technical signals.** `modules/technical/signals.py` keeps an explicit whitelist (`SIGNAL_INDEXES`). Widening it is a separate decision outside this plan.
- No new dependency, generic provider abstraction, or user-supplied contract-code parameter.
- Preserve user changes already present in the worktree.

---

## File Map

- Modify `apps/models/market/series.py`: add nullable `IndexFutureDaily.contract_code`.
- Create `migrations/versions/f8a2c6d9e104_add_future_daily_contract_code.py`: add/drop the column from the current head (`b6d02f5a91c7` when this plan was written), using the repository's `upgrade(engine_name)` dispatch shape.
- Modify `airflow/sql/postgres/index_future_daily/upsert.sql`: write and update `contract_code`.
- Modify `airflow/modules/collectors/market/yahoo.py`: pass `None` for Yahoo continuous-future rows.
- Create `airflow/modules/collectors/market/kis_future_daily.py`: request, validate, roll-filter, and store future daily bars.
- Create `airflow/dags/kis_future_daily.py`: schedule, parameters, calendar, retry, and per-symbol transactions.
- Create `tests/collectors/test_kis_future_daily.py`: collector, roll, storage, and SQL contract tests.
- Create `tests/dags/test_kis_future_daily.py`: DAG metadata, date parsing, windows, schedule, and holiday tests.
- Modify `tests/collectors/test_yahoo.py`: assert Yahoo writes nullable `contract_code`.
- Modify `tests/models/test_market_models.py`: assert the model column contract.
- Modify `tests/migrations/test_quote_split_revision.py`: assert offline migration SQL and view compatibility.

### Task 1: Read the measured contract facts (probe already done)

**Files:**
- Read: `docs/collection/kis-index-daily-collection.md` sections 4.1, 4.2, 4.4, 4.4.1

The probe ran on 2026-08-27 against the production app key, read-only, and section 4.4 holds the dated result table. **Do not re-run it.** Read the section and carry these six facts into the collector; each one contradicts something the earlier draft of this plan assumed.

- [ ] **Step 1: Absorb what the measurement changed**

```text
1. No code translation. `A01609`/`A06609` work on the daily API; the official
   example's `101W09` returns zero rows. `front_contract()`'s string format is
   already correct, so `daily_contract_code()` does not exist and is not needed.
2. Expired contracts are queryable. `A01606` returned 69 rows through its
   20260611 expiry, so backfill needs no start-date floor.
3. `expiry_date()` matches reality: the expired contract's last bar IS the
   expiry day, which is what section 4.3's roll rule assumes.
4. `output1` is an empty dict `{}` for expired contracts. The echoed-code check
   must be "if present, must match" — a required check fails every backfill window.
5. `output2` never carries `futs_shrn_iscd`. `contract_code` comes from the
   requested window, not from the response.
6. Row cap is 100 and the `tr_cont` header is empty. Window walking is the only
   paging mechanism, and 200 calendar days is two pages.
```

- [ ] **Step 2: Confirm the enumeration gap is still open**

Section 4.4.1 describes the one function this plan must add: something that walks a past date range quarter by quarter using `CONTRACT_MONTHS` and `expiry_date()`, ending each window on the expiry day inclusive. `front_contract()` cannot do it. Task 3 writes it.

### Task 2: Add contract provenance to future daily storage

**Files:**
- Modify: `apps/models/market/series.py`
- Create: `migrations/versions/f8a2c6d9e104_add_future_daily_contract_code.py`
- Modify: `airflow/sql/postgres/index_future_daily/upsert.sql`
- Modify: `airflow/modules/collectors/market/yahoo.py`
- Modify: `tests/models/test_market_models.py`
- Modify: `tests/migrations/test_quote_split_revision.py`
- Modify: `tests/collectors/test_yahoo.py`

**Interfaces:**
- Produces: nullable `IndexFutureDaily.contract_code: str | None` and a 10-value upsert row `(provider, symbol, business_date, open, high, low, close, volume, contract_code, source_record_id)`.

- [ ] **Step 1: Write failing model, SQL, migration, and Yahoo tests**

```python
def test_future_daily_preserves_the_contract_code():
    from apps.models.market import IndexFutureDaily

    column = IndexFutureDaily.__table__.c.contract_code
    assert column.nullable is True


def test_future_daily_contract_code_is_added_without_changing_the_view(capsys):
    sql = head_sql(capsys)
    assert "ALTER TABLE index_future_daily ADD COLUMN contract_code TEXT" in sql
    assert "CREATE VIEW quote_daily" in sql
```

Extend the Yahoo daily row-order test to unpack `contract_code` before `source_record_id` and assert it is `None`.

- [ ] **Step 2: Run the focused tests and confirm failure**

```bash
uv run pytest tests/models/test_market_models.py tests/migrations/test_quote_split_revision.py tests/collectors/test_yahoo.py -q
```

Expected: FAIL because the model and SQL do not contain `contract_code`.

- [ ] **Step 3: Add the nullable model column**

```python
class IndexFutureDaily(MacroDailyColumns, EntityBase):
    __tablename__ = "index_future_daily"
    __table_args__ = _daily_table_args(
        "index_future_daily",
        "지수선물의 일봉을 상관 분석용으로 누적하는 테이블",
    )

    contract_code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="KIS 국내선물 실제 월물 단축코드. Yahoo 연속 심볼은 NULL",
    )
```

- [ ] **Step 4: Add the hand-written migration**

First read `migrations/versions/` and confirm the single head. At the time this plan was written it was `b6d02f5a91c7` (`add_cached_prompt_tokens`). If a newer head has landed, rebase this migration on it before editing the revision file.

Do not run `uv run alembic ...` directly. The alias list comes from `config.yaml` through `migrations/cli.py`, so the entry point is `just migrate <args>`. Autogenerate is forbidden here anyway — `config.yaml` points at the production database — so this revision is written by hand.

**The revision must use the multi-alias dispatch shape.** `migrations/env.py` calls `upgrade(engine_name)`, not `upgrade()`. Copy the structure from `migrations/versions/b6d02f5a91c7_add_cached_prompt_tokens.py`. The column comment must match the model's comment character for character, or the next autogenerate reports a permanent diff.

```python
revision: str = "f8a2c6d9e104"
down_revision: str | Sequence[str] | None = "b6d02f5a91c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CONTRACT_CODE_COMMENT = "KIS 국내선물 실제 월물 단축코드. Yahoo 연속 심볼은 NULL"


def upgrade(engine_name: str) -> None:
    _run(f"upgrade_{engine_name}")


def downgrade(engine_name: str) -> None:
    _run(f"downgrade_{engine_name}")


def _run(name: str) -> None:
    operations = globals().get(name)
    if operations is not None:
        operations()


def upgrade_default() -> None:
    op.add_column(
        "index_future_daily",
        sa.Column("contract_code", sa.Text(), nullable=True, comment=CONTRACT_CODE_COMMENT),
    )


def downgrade_default() -> None:
    op.drop_column("index_future_daily", "contract_code")
```

- [ ] **Step 5: Extend the upsert and both writers**

Add `contract_code` before `source_record_id` in the INSERT and set `contract_code = EXCLUDED.contract_code` on conflict. In Yahoo `_daily_upserts()`, insert `None` in that position only when `symbol.kind == "index_future"`.

- [ ] **Step 6: Run focused tests and offline migration SQL**

```bash
uv run pytest tests/models/test_market_models.py tests/migrations/test_quote_split_revision.py tests/collectors/test_yahoo.py -q
```

Expected: PASS. The offline SQL is already the subject under test — `tests/helpers.head_sql()` runs `alembic upgrade head --sql` across every alias in-process and hands back the emitted SQL. Do not shell out to `alembic`; bare CLI calls miss the alias list that `migrations/cli.py` injects.

- [ ] **Step 7: Commit the schema contract**

```bash
git add apps/models/market/series.py migrations/versions/f8a2c6d9e104_add_future_daily_contract_code.py airflow/sql/postgres/index_future_daily/upsert.sql airflow/modules/collectors/market/yahoo.py tests/models/test_market_models.py tests/migrations/test_quote_split_revision.py tests/collectors/test_yahoo.py
git commit -m "feat(market): preserve future daily contracts"
```

### Task 3: Implement the pure future-daily collector

**Files:**
- Create: `airflow/modules/collectors/market/kis_future_daily.py`
- Create: `tests/collectors/test_kis_future_daily.py`

**Interfaces:**
- Consumes: the measured `A0` code format (spec 4.4); `DomesticFuture`, `CONTRACT_MONTHS`, `expiry_date`, `send_get`, `result_error`, `SOURCE_RECORD_INSERT`, `INDEX_FUTURE_DAILY_UPSERT`, and `DailyIndexBar` from `modules.collectors.market.kis_index_daily`.
- Produces:
  - `FutureContractWindow(future, contract_code, start_date, end_date)`
  - `DailyFutureBar(DailyIndexBar)` — the validated OHLCV bar plus `contract_code`. **Subclass, do not retype.** `DailyIndexBar` already carries the positive-finite OHLC validators, the consistent-range model validator, and `volume: int = Field(ge=0)`. Copying them means one of the two copies eventually drifts.
  - `FutureDailyFetch(symbol, contracts, start_date, end_date, bars, page_count, started_at, completed_at)`
  - `contract_windows(future, start_date, end_date) -> tuple[FutureContractWindow, ...]`
  - `KisFutureDailyCollector.fetch(future, start_date, end_date, *, sleep=0.5) -> FutureDailyFetch`
  - `KisFutureDailyCollector.store(connection, fetch) -> int`

- [ ] **Step 1: Write fixtures and failing validation tests**

Create a small JSON builder with two reverse-ordered rows and assert:

```python
def test_fetch_sends_the_verified_daily_contract(monkeypatch):
    fetch = collector().fetch(
        DomesticFuture.KOSPI200_FUT,
        date(2026, 9, 10),
        date(2026, 9, 11),
        sleep=0,
    )
    assert fetch.symbol == "KOSPI200_FUT"
    assert [bar.business_date for bar in fetch.bars] == [date(2026, 9, 10), date(2026, 9, 11)]


def test_roll_keeps_expiry_day_and_switches_the_next_day():
    windows = contract_windows(
        DomesticFuture.KOSPI200_FUT,
        date(2026, 9, 10),
        date(2026, 9, 11),
    )
    assert windows[0].end_date == date(2026, 9, 10)
    assert windows[1].start_date == date(2026, 9, 11)
```

Use the measured contract codes in the fixture assertions: `A01609` for the September 2026 KOSPI200 contract, `A01606` for the expired June one, `A06609`/`A06606` for KOSDAQ150. Also test malformed JSON, `rt_cd`, missing `output2`, duplicate/out-of-range dates, non-finite or non-positive OHLC, inconsistent high/low, negative volume, empty total result, and page-cap exhaustion, plus a response whose `output1` is `{}` (the expired-contract shape, which must pass) and one whose `output1.futs_shrn_iscd` disagrees with the request (which must fail).

- [ ] **Step 2: Run the new test file and confirm import failure**

```bash
uv run pytest tests/collectors/test_kis_future_daily.py -q
```

Expected: FAIL because `kis_future_daily` does not exist.

- [ ] **Step 3: Implement the contract enumerator, the models, and the request loop**

`contract_windows()` walks the requested range quarter by quarter. For each month in `CONTRACT_MONTHS` whose `expiry_date()` falls at or after the window cursor, emit one `FutureContractWindow` ending on that expiry date (inclusive) and start the next window on the following day. **Do not call `front_contract()`** — it takes `today` and answers only which contract trades now, so a range that crosses an expiry would be requested entirely against the current contract. Build the short code from the year and month with `front_contract()`'s format, `A0{product_digit}{year % 10}{month:02d}`, which the probe confirmed on the daily endpoint. Take year and month as arguments so the one-digit-year limit (spec 4.4.1) is a single-line change later.

Map `stck_bsop_date`, `futs_oprc`, `futs_hgpr`, `futs_lwpr`, `futs_prpr`, and `acml_vol` into a frozen Pydantic response row. Convert them to a validated `DailyFutureBar` (the `DailyIndexBar` subclass) and attach the requested window's `contract_code`. For each `FutureContractWindow`, request only its inclusive date range, keep only dates inside that window, and deduplicate globally by `business_date`. Page by window walking only: set the next end date to one day before the oldest returned date and stop on an empty page. **Do not branch on `tr_cont`.** The probe measured an empty header on every response (spec section 3), so that branch would only ever run in tests, and window walking stays correct if KIS ever starts sending it. Reject a duplicate, a response outside its window, an empty combined result, or more than ten pages. `output2` arrives newest-first; return bars sorted ascending.

The echoed-code check is conditional: `output1` is `{}` for expired contracts, so compare `futs_shrn_iscd` to the requested code **only when the field is present**. A required check fails every backfill window.

- [ ] **Step 4: Implement storage with contract provenance**

For each bar execute a row shaped as:

```python
(
    SOURCE,
    fetch.symbol,
    bar.business_date,
    bar.open,
    bar.high,
    bar.low,
    bar.close,
    bar.volume,
    bar.contract_code,
    source_record_id,
)
```

Metadata must include `symbol`, `start_date`, `end_date`, `contracts`, `page_count`, and `bar_count`; payload is `None`.

- [ ] **Step 5: Run collector tests and static checks**

```bash
uv run pytest tests/collectors/test_kis_future_daily.py tests/collectors/test_yahoo.py -q
uv run ruff check airflow/modules/collectors/market/kis_future_daily.py tests/collectors/test_kis_future_daily.py
uv run pyrefly check airflow/modules/collectors/market/kis_future_daily.py
```

Expected: all commands exit 0.

- [ ] **Step 6: Commit the collector**

```bash
git add airflow/modules/collectors/market/kis_future_daily.py tests/collectors/test_kis_future_daily.py
git commit -m "feat(kis): collect domestic future daily bars"
```

### Task 4: Add the Airflow DAG

**Files:**
- Create: `airflow/dags/kis_future_daily.py`
- Create: `tests/dags/test_kis_future_daily.py`

**Interfaces:**
- Consumes: `KisFutureDailyCollector`, `DomesticFuture`, `business_date`, and `krx_open_day`.
- Produces: DAG `kis_future_daily`, schedule `30 18 * * 1-5`, parameters `start_date` and `end_date`.

- [ ] **Step 1: Write failing DAG tests**

```python
def test_the_future_daily_dag_runs_between_index_bars_and_signals():
    assert kis_future_daily.kis_future_daily.schedule == "30 18 * * 1-5"
    assert kis_future_daily.kis_future_daily.max_active_runs == 1


def test_the_default_span_is_200_calendar_days():
    end = date(2026, 8, 27)
    assert kis_future_daily.span_start(end) == date(2026, 2, 8)
```

Also assert these exact contracts: a non-ISO parameter raises `AirflowFailException`; `start_date > end_date` raises `AirflowFailException`; `fetch_windows(date(2025, 1, 1), date(2026, 8, 27))` returns ascending, non-overlapping inclusive windows that advance by 200 calendar days; and the task skips only when `krx_open_day()` returns literal `False`.

**Match `kis_index_daily` on both points, do not invent a second contract.** Its `_calendar_day()` raises `AirflowFailException` because a malformed parameter is a configuration error that a retry cannot fix. Its `fetch_windows()` steps the cursor by 200 days and makes the window end inclusive, so a window spans 201 calendar days; asserting "at most 200" would fail against copied code. Copy the function and assert the step, not the span.

- [ ] **Step 2: Run and confirm import failure**

```bash
uv run pytest tests/dags/test_kis_future_daily.py -q
```

Expected: FAIL because the DAG module does not exist.

- [ ] **Step 3: Implement the DAG using the existing domestic-index runbook**

Parse optional `start_date` and `end_date` as ISO dates with the same shape-first regex and `AirflowFailException` as `kis_index_daily._calendar_day()`; default the end to the run-derived Seoul business date and the start to 200 calendar days earlier. Split an explicit longer range into ascending, non-overlapping inclusive windows that advance by 200 days. Skip only when an automatic run's `krx_open_day()` result is literal `False`. Use one task, one shared token per run, one DB connection, and one transaction per symbol/window. Iterate `DomesticFuture`; retry recoverable credential/token failures once, fail immediately on unrecoverable HTTP or time-window errors, aggregate payload/result/connection failures by symbol, and raise once after all symbols.

- [ ] **Step 4: Run DAG and collector tests**

```bash
uv run pytest tests/dags/test_kis_future_daily.py tests/collectors/test_kis_future_daily.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit the DAG**

```bash
git add airflow/dags/kis_future_daily.py tests/dags/test_kis_future_daily.py
git commit -m "feat(kis): schedule domestic future daily bars"
```

### Task 5: Full verification and graph refresh

- [ ] **Step 1: Run the market collector and migration regression set**

```bash
uv run pytest tests/collectors/test_kis_future_daily.py tests/collectors/test_yahoo.py tests/dags/test_kis_future_daily.py tests/migrations -q
uv run pytest tests/models/test_market_models.py tests/modules/test_technical.py tests/dags/test_technical_signal_daily.py -q
uv run ruff check airflow apps migrations tests
uv run pyrefly check airflow apps
```

Expected: all commands exit 0. `tests/migrations` runs whole because the new revision changes the offline SQL that every migration test reads.

- [ ] **Step 2: Update the graph**

```bash
graphify update .
```

Expected: the new collector, DAG, model column, and tests appear without extraction errors.

- [ ] **Step 3: Commit graph output only if changed**

```bash
git add graphify-out
git commit -m "docs(graph): add future daily collection"
```

Skip when `git status --short graphify-out` is empty.
