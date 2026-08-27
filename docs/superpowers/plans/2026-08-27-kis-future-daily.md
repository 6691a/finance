# KIS Domestic Future Daily Bars Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collect official daily OHLCV for `KOSPI200_FUT` and `KOSDAQ150_FUT` while preserving the actual KIS contract code on every row.

**Architecture:** Add one focused collector and one Airflow DAG around KIS `inquire-daily-fuopchartprice`. Extend only `index_future_daily` with a nullable `contract_code`; Yahoo continuous futures continue writing `NULL`. Resolve the mismatch between intraday codes (`A01609`) and official daily short codes (`101W09` form) with a mandatory read-only probe before implementation.

**Tech Stack:** Python 3.13, Apache Airflow 3.3, Pydantic, PostgreSQL, Alembic, pytest

**Spec:** `docs/collection/kis-index-daily-collection.md` sections 1–4 and 7–12

## Global Constraints

- Do not begin schema or collector work until Task 1 passes and its evidence is recorded in the spec.
- Use `/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice`, TR ID `FHKIF03020100`, market `F`, period `D`.
- Store the logical symbols `KOSPI200_FUT` and `KOSDAQ150_FUT`; store the exact queried short code in `contract_code`.
- The front contract includes expiry day and rolls on the following trading day. Do not volume-roll or price-adjust.
- No new dependency, generic provider abstraction, or user-supplied contract-code parameter.
- Preserve user changes already present in the worktree.

---

## File Map

- Modify `docs/collection/kis-index-daily-collection.md`: record probe facts and remove the implementation gate after verification.
- Modify `apps/models/market/series.py`: add nullable `IndexFutureDaily.contract_code`.
- Create `migrations/versions/f8a2c6d9e104_add_future_daily_contract_code.py`: add/drop the column from current head `e7d3b1f094ac`.
- Modify `airflow/sql/postgres/index_future_daily/upsert.sql`: write and update `contract_code`.
- Modify `airflow/modules/collectors/market/yahoo.py`: pass `None` for Yahoo continuous-future rows.
- Create `airflow/modules/collectors/market/kis_future_daily.py`: request, validate, roll-filter, and store future daily bars.
- Create `airflow/dags/kis_future_daily.py`: schedule, parameters, calendar, retry, and per-symbol transactions.
- Create `tests/collectors/test_kis_future_daily.py`: collector, roll, storage, and SQL contract tests.
- Create `tests/dags/test_kis_future_daily.py`: DAG metadata, date parsing, windows, schedule, and holiday tests.
- Modify `tests/collectors/test_yahoo.py`: assert Yahoo writes nullable `contract_code`.
- Modify `tests/models/test_market_models.py`: assert the model column contract.
- Modify `tests/migrations/test_quote_split_revision.py`: assert offline migration SQL and view compatibility.

### Task 1: Probe and freeze the KIS contract-code contract

**Files:**
- Modify: `docs/collection/kis-index-daily-collection.md:112-128`

**Interfaces:**
- Produces: verified mapping rules consumed by `daily_contract_code()` and sanitized response fixtures used by collector tests.
- Blocks: every later task in this plan.

- [ ] **Step 1: Verify credentials without printing them**

```bash
python - <<'PY'
from pathlib import Path

values = {}
for line in Path("compose/local/airflow/.env").read_text().splitlines():
    if "=" in line and not line.lstrip().startswith("#"):
        key, value = line.split("=", 1)
        values[key] = value
assert values.get("KIS_APP_KEY")
assert values.get("KIS_APP_SECRET")
print("KIS credentials available")
PY
```

- [ ] **Step 2: Run a temporary read-only probe outside the repository**

Create the script under `$(mktemp -d)` and use the existing `access_token()`/`send_get()` functions. It must call:

```python
send_get(
    token,
    app_key,
    app_secret,
    "/uapi/domestic-futureoption/v1/quotations/display-board-futures",
    "FHPIF05030200",
    {
        "FID_COND_MRKT_DIV_CODE": "F",
        "FID_COND_SCR_DIV_CODE": "20503",
        "FID_COND_MRKT_CLS_CODE": "MKI",
    },
)
```

From the response, print only `futs_shrn_iscd`, `hts_kor_isnm`, and `hts_rmnn_dynu`. Never print headers, tokens, or the full response.

- [ ] **Step 3: Probe daily bars for both front contracts and one expired contract**

For every selected short code call:

```python
send_get(
    token,
    app_key,
    app_secret,
    "/uapi/domestic-futureoption/v1/quotations/inquire-daily-fuopchartprice",
    "FHKIF03020100",
    {
        "FID_COND_MRKT_DIV_CODE": "F",
        "FID_INPUT_ISCD": short_code,
        "FID_INPUT_DATE_1": "20260301",
        "FID_INPUT_DATE_2": "20260827",
        "FID_PERIOD_DIV_CODE": "D",
    },
)
```

Record only: requested code, returned `futs_shrn_iscd`, first/last `stck_bsop_date`, row count, output order, response `tr_cont`, and whether the expired contract returns rows.

- [ ] **Step 4: Verify six assertions before continuing**

```text
1. KOSPI200 and KOSDAQ150 can be selected without matching localized labels alone.
2. Both daily requests return rt_cd=0 and non-empty output2.
3. output2 has stck_bsop_date, futs_oprc, futs_hgpr, futs_lwpr, futs_prpr, acml_vol.
4. The response echoes or otherwise unambiguously identifies the requested contract.
5. The exact code rule can produce the contract before and after one quarterly expiry.
6. Row cap and tr_cont behavior are known.
```

If any assertion fails, stop this plan and revise the design; do not invent a string conversion.

- [ ] **Step 5: Record evidence in the spec and commit it**

Replace the 4.4 checklist with a dated result table containing the observed codes and pagination behavior. Do not store credentials or full payloads.

```bash
git add docs/collection/kis-index-daily-collection.md
git commit -m "docs(kis): record future daily API probe"
```

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

First run `uv run alembic heads` and require the output to be `e7d3b1f094ac (head)`. If the head has changed, stop and rebase this migration on the new single head before editing the revision file.

```python
revision = "f8a2c6d9e104"
down_revision = "e7d3b1f094ac"


def upgrade() -> None:
    op.add_column(
        "index_future_daily",
        sa.Column(
            "contract_code",
            sa.Text(),
            nullable=True,
            comment="KIS 국내선물 실제 월물 단축코드. Yahoo 연속 심볼은 NULL",
        ),
    )


def downgrade() -> None:
    op.drop_column("index_future_daily", "contract_code")
```

- [ ] **Step 5: Extend the upsert and both writers**

Add `contract_code` before `source_record_id` in the INSERT and set `contract_code = EXCLUDED.contract_code` on conflict. In Yahoo `_daily_upserts()`, insert `None` in that position only when `symbol.kind == "index_future"`.

- [ ] **Step 6: Run focused tests and offline migration SQL**

```bash
uv run pytest tests/models/test_market_models.py tests/migrations/test_quote_split_revision.py tests/collectors/test_yahoo.py -q
uv run alembic upgrade head --sql >/tmp/kis-future-daily-migration.sql
rg -n "index_future_daily.*contract_code|ADD COLUMN contract_code" /tmp/kis-future-daily-migration.sql
```

Expected: tests PASS and offline SQL contains the nullable column addition.

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
- Consumes: the exact code rule recorded by Task 1; `DomesticFuture`, `CONTRACT_MONTHS`, `expiry_date`, `send_get`, `result_error`, `SOURCE_RECORD_INSERT`, `INDEX_FUTURE_DAILY_UPSERT`.
- Produces:
  - `FutureContractWindow(future, contract_code, start_date, end_date)`
  - `DailyFutureBar(business_date, open, high, low, close, volume, contract_code)`
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

Use the exact contract codes recorded in Task 1 in the fixture assertions. Also test malformed JSON, `rt_cd`, missing `output2`, duplicate/out-of-range dates, non-finite or non-positive OHLC, inconsistent high/low, negative volume, empty total result, and page-cap exhaustion.

- [ ] **Step 2: Run the new test file and confirm import failure**

```bash
uv run pytest tests/collectors/test_kis_future_daily.py -q
```

Expected: FAIL because `kis_future_daily` does not exist.

- [ ] **Step 3: Implement the models and request loop**

Map `stck_bsop_date`, `futs_oprc`, `futs_hgpr`, `futs_lwpr`, `futs_prpr`, and `acml_vol` into a frozen Pydantic response row. Convert them to a validated `DailyFutureBar` and attach the requested window's `contract_code`. For each `FutureContractWindow`, request only its inclusive date range, keep only dates inside that window, and deduplicate globally by `business_date`. Stop on an empty page; when response `tr_cont` is `M` or `F`, send the next request with `tr_cont="N"`; otherwise set the next end date to one day before the oldest returned date. Reject a duplicate, a response outside its window, an empty combined result, or more than ten pages. Return bars sorted ascending.

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

Also assert these exact contracts: a non-ISO parameter raises `ValueError`; `start_date > end_date` raises `ValueError`; `fetch_windows(date(2025, 1, 1), date(2026, 8, 27))` returns ascending, non-overlapping inclusive windows of at most 200 calendar days; and the task skips only when `krx_open_day()` returns literal `False`.

- [ ] **Step 2: Run and confirm import failure**

```bash
uv run pytest tests/dags/test_kis_future_daily.py -q
```

Expected: FAIL because the DAG module does not exist.

- [ ] **Step 3: Implement the DAG using the existing domestic-index runbook**

Parse optional `start_date` and `end_date` as ISO dates; default the end to the run-derived Seoul business date and the start to 200 calendar days earlier. Split an explicit longer range into ascending, non-overlapping inclusive windows of at most 200 days. Skip only when an automatic run's `krx_open_day()` result is literal `False`. Use one task, one shared token per run, one DB connection, and one transaction per symbol/window. Iterate `DomesticFuture`; retry recoverable credential/token failures once, fail immediately on unrecoverable HTTP or time-window errors, aggregate payload/result/connection failures by symbol, and raise once after all symbols.

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
uv run pytest tests/collectors/test_kis_future_daily.py tests/collectors/test_yahoo.py tests/dags/test_kis_future_daily.py tests/migrations/test_quote_split_revision.py tests/models/test_market_models.py tests/modules/test_technical.py tests/dags/test_technical_signal_daily.py -q
uv run ruff check airflow apps migrations tests
uv run pyrefly check airflow apps
```

Expected: all commands exit 0.

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
