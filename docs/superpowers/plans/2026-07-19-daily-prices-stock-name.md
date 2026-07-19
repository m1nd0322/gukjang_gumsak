# Daily Prices Stock Name Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a synchronized nullable `name` column to every `daily_prices` row, backfill existing data, and keep future price writes readable by stock name without changing the backtest price API.

**Architecture:** Keep `ticker_map` as the source of truth and store `daily_prices.name` as a denormalized lookup copy. `StockDB` performs an idempotent startup migration, resolves the current name during price upserts, and synchronizes all affected price rows in the same transaction as ticker-map imports or refreshes.

**Tech Stack:** Python 3.11, DuckDB, standard `unittest`, `unittest.mock`, `uv`, Ruff

## Global Constraints

- `daily_prices.name` is nullable `VARCHAR`; a ticker without a `ticker_map` row is stored as `NULL`.
- `ticker_map.name` remains the source of truth and renames update historical `daily_prices` rows for the same ticker.
- The existing `(ticker, date)` primary key and all OHLCV meanings remain unchanged.
- `save_prices(ticker: str, data: List[dict])` keeps its current call signature.
- `get_prices()` keeps returning only `date`, `open`, `high`, `low`, `close`, and `volume`.
- New and migrated databases expose `name` as the final `daily_prices` column so `SELECT *` has deterministic column order.
- Ticker-map upsert and price-name synchronization occur in one DuckDB transaction.
- No new dependency or name index is added.
- `stock_data.duckdb` remains Git-ignored; migrate it locally only after automated validation passes.
- Preserve the unrelated untracked `get-pip.py` file and never stage it.

## File Structure

- Modify `stock_db.py`: own the schema migration, shared name synchronization SQL, transactional ticker-map synchronization, and price-name upsert behavior.
- Modify `tests/test_stock_db.py`: lock new-schema, legacy migration, mapped/unmapped writes, rename synchronization, rollback, DB viewer, and `get_prices()` compatibility.
- Modify `README.md`: document that `daily_prices` now stores ticker, stock name, and OHLCV, including `NULL` and backfill behavior.

---

### Task 1: Add the idempotent schema migration and legacy backfill

**Files:**
- Modify: `stock_db.py:48-112`
- Test: `tests/test_stock_db.py:1-35`

**Interfaces:**
- Consumes: existing `StockDB._connect()` and DuckDB `Connection.execute()`.
- Produces: `StockDB._sync_daily_price_names(con) -> None`; a `daily_prices.name VARCHAR` column on every initialized database.

- [ ] **Step 1: Write failing new-schema and legacy-migration tests**

Add `import duckdb` beside the third-party imports in `tests/test_stock_db.py`:

```python
import duckdb
import pandas as pd
```

Add these methods at the start of `StockDbCacheTest`, after `tearDown()`:

```python
    def test_new_daily_prices_schema_includes_nullable_name(self):
        connection = self.db._connect()
        try:
            columns = connection.execute(
                "PRAGMA table_info('daily_prices')"
            ).fetchall()
        finally:
            connection.close()

        name_column = next(row for row in columns if row[1] == "name")
        self.assertEqual(columns[-1][1], "name")
        self.assertEqual(name_column[2], "VARCHAR")
        self.assertFalse(name_column[3])

    def test_legacy_daily_prices_schema_is_migrated_and_backfilled(self):
        handle = tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False)
        legacy_path = handle.name
        handle.close()
        os.unlink(legacy_path)

        try:
            connection = duckdb.connect(legacy_path)
            try:
                connection.execute("""
                    CREATE TABLE daily_prices (
                        ticker VARCHAR NOT NULL,
                        date DATE NOT NULL,
                        open DOUBLE,
                        high DOUBLE,
                        low DOUBLE,
                        close DOUBLE,
                        volume BIGINT,
                        PRIMARY KEY (ticker, date)
                    )
                """)
                connection.execute("""
                    CREATE TABLE ticker_map (
                        ticker VARCHAR PRIMARY KEY,
                        name VARCHAR NOT NULL,
                        market VARCHAR,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                connection.execute(
                    "INSERT INTO ticker_map (ticker, name) VALUES (?, ?)",
                    ["005930", "삼성전자"],
                )
                connection.execute("""
                    INSERT INTO daily_prices
                        (ticker, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, ["005930", "2026-01-05", 70000, 71000, 69500, 70500, 1000])
            finally:
                connection.close()

            StockDB(legacy_path)

            connection = duckdb.connect(legacy_path)
            try:
                columns = connection.execute(
                    "PRAGMA table_info('daily_prices')"
                ).fetchall()
                row = connection.execute(
                    "SELECT ticker, name FROM daily_prices"
                ).fetchone()
            finally:
                connection.close()

            self.assertEqual(columns[-1][1], "name")
            self.assertEqual(row, ("005930", "삼성전자"))
        finally:
            if os.path.exists(legacy_path):
                os.unlink(legacy_path)
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db.StockDbCacheTest.test_new_daily_prices_schema_includes_nullable_name tests.test_stock_db.StockDbCacheTest.test_legacy_daily_prices_schema_is_migrated_and_backfilled -v
```

Expected: both tests fail because `daily_prices` has no `name` column and legacy initialization does not alter or backfill the table.

- [ ] **Step 3: Implement the shared synchronization SQL and startup migration**

Add this method after `_connect()` in `stock_db.py`:

```python
    @staticmethod
    def _sync_daily_price_names(con) -> None:
        """ticker_map을 기준으로 저장된 일봉 종목명을 동기화한다."""
        con.execute("""
            UPDATE daily_prices AS dp
            SET name = tm.name
            FROM ticker_map AS tm
            WHERE dp.ticker = tm.ticker
              AND dp.name IS DISTINCT FROM tm.name
        """)
```

Change the `daily_prices` creation SQL so `name` is the last non-key column:

```python
            con.execute("""
                CREATE TABLE IF NOT EXISTS daily_prices (
                    ticker VARCHAR NOT NULL,
                    date DATE NOT NULL,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    volume BIGINT,
                    name VARCHAR,
                    PRIMARY KEY (ticker, date)
                )
            """)
```

Immediately after the `ticker_map` creation statement and before the other table creation statements, add the idempotent migration and backfill:

```python
            con.execute(
                "ALTER TABLE daily_prices "
                "ADD COLUMN IF NOT EXISTS name VARCHAR"
            )
            self._sync_daily_price_names(con)
```

- [ ] **Step 4: Re-run the focused tests and the stock DB suite**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db.StockDbCacheTest.test_new_daily_prices_schema_includes_nullable_name tests.test_stock_db.StockDbCacheTest.test_legacy_daily_prices_schema_is_migrated_and_backfilled -v
```

Expected: 2 tests pass.

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db -v
```

Expected: the complete `tests.test_stock_db` suite passes.

- [ ] **Step 5: Commit the migration checkpoint**

```bash
git add stock_db.py tests/test_stock_db.py
git commit -m "기존 가격 이력이 종목명을 자동으로 회복하게 한다" -m "Constraint: 구형 DuckDB 스키마와 기존 기본키를 유지한다
Confidence: high
Scope-risk: narrow
Directive: daily_prices의 추가 컬럼은 name을 마지막에 유지한다
Tested: unittest schema migration and stock_db suite"
```

---

### Task 2: Persist names on every price upsert without breaking price consumers

**Files:**
- Modify: `stock_db.py:387-416`
- Test: `tests/test_stock_db.py`

**Interfaces:**
- Consumes: `ticker_map(ticker, name)` and `save_prices(ticker: str, data: List[dict])`.
- Produces: inserted or updated `daily_prices.name`; unchanged `get_prices()` return dictionaries.

- [ ] **Step 1: Write failing mapped-write, preservation, unmapped, and viewer tests**

Add these methods to `StockDbCacheTest`:

```python
    def test_save_prices_persists_name_and_does_not_erase_it_without_mapping(self):
        connection = self.db._connect()
        try:
            connection.execute(
                "INSERT INTO ticker_map (ticker, name) VALUES (?, ?)",
                ["005930", "삼성전자"],
            )
        finally:
            connection.close()

        self.db.save_prices("005930", [{
            "date": "2026-01-05",
            "open": 70000,
            "high": 71000,
            "low": 69500,
            "close": 70500,
            "volume": 1000,
        }])

        connection = self.db._connect()
        try:
            connection.execute(
                "DELETE FROM ticker_map WHERE ticker = ?", ["005930"]
            )
        finally:
            connection.close()

        self.db.save_prices("005930", [{
            "date": "2026-01-05",
            "open": 70500,
            "high": 71500,
            "low": 70000,
            "close": 71200,
            "volume": 1200,
        }])

        connection = self.db._connect()
        try:
            row = connection.execute(
                "SELECT name, close FROM daily_prices "
                "WHERE ticker = ? AND date = ?",
                ["005930", "2026-01-05"],
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(row, ("삼성전자", 71200.0))

    def test_save_prices_stores_null_name_for_unmapped_ticker(self):
        self.db.save_prices("999999", [{
            "date": "2026-01-05",
            "open": 1000,
            "high": 1100,
            "low": 900,
            "close": 1050,
            "volume": 100,
        }])

        page = self.db.query_table(
            "daily_prices", filter_col="ticker", filter_val="999999"
        )

        self.assertEqual(page["total"], 1)
        self.assertIsNone(page["rows"][0]["name"])

    def test_daily_prices_viewer_filters_by_stored_name(self):
        connection = self.db._connect()
        try:
            connection.execute(
                "INSERT INTO ticker_map (ticker, name) VALUES (?, ?)",
                ["005930", "삼성전자"],
            )
        finally:
            connection.close()
        self.db.save_prices("005930", [{
            "date": "2026-01-05",
            "open": 70000,
            "high": 71000,
            "low": 69500,
            "close": 70500,
            "volume": 1000,
        }])

        page = self.db.query_table(
            "daily_prices", filter_col="name", filter_val="삼성"
        )
        prices = self.db.get_prices(
            "005930", "2026-01-05", "2026-01-05"
        )

        self.assertEqual(page["total"], 1)
        self.assertEqual(page["rows"][0]["name"], "삼성전자")
        self.assertEqual(
            set(prices[0]),
            {"date", "open", "high", "low", "close", "volume"},
        )
```

- [ ] **Step 2: Run the focused tests and verify the mapped-name assertions fail**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db.StockDbCacheTest.test_save_prices_persists_name_and_does_not_erase_it_without_mapping tests.test_stock_db.StockDbCacheTest.test_save_prices_stores_null_name_for_unmapped_ticker tests.test_stock_db.StockDbCacheTest.test_daily_prices_viewer_filters_by_stored_name -v
```

Expected: mapped-name and name-filter tests fail because `save_prices()` does not populate `name`; the explicit unmapped-`NULL` regression may already pass.

- [ ] **Step 3: Add name resolution to `save_prices()`**

Replace the body inside the `try` block of `save_prices()` with:

```python
            name_row = con.execute(
                "SELECT name FROM ticker_map WHERE ticker = ?",
                [ticker],
            ).fetchone()
            name = name_row[0] if name_row else None
            rows = [
                (
                    ticker,
                    d['date'],
                    d['open'],
                    d['high'],
                    d['low'],
                    d['close'],
                    d['volume'],
                    name,
                )
                for d in data
            ]
            con.executemany("""
                INSERT INTO daily_prices
                    (ticker, date, open, high, low, close, volume, name)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker, date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume,
                    name = COALESCE(EXCLUDED.name, daily_prices.name)
            """, rows)
            logger.debug(f"  {ticker}: {len(rows)}일 저장")
```

The `COALESCE` expression is required: a temporary missing mapping must not erase an already stored name.

- [ ] **Step 4: Re-run focused and module tests**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db.StockDbCacheTest.test_save_prices_persists_name_and_does_not_erase_it_without_mapping tests.test_stock_db.StockDbCacheTest.test_save_prices_stores_null_name_for_unmapped_ticker tests.test_stock_db.StockDbCacheTest.test_daily_prices_viewer_filters_by_stored_name -v
```

Expected: 3 tests pass, and `get_prices()` still has exactly six keys.

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db -v
```

Expected: the complete `tests.test_stock_db` suite passes.

- [ ] **Step 5: Commit the price-write checkpoint**

```bash
git add stock_db.py tests/test_stock_db.py
git commit -m "가격 저장만으로도 종목을 바로 식별할 수 있게 한다" -m "Constraint: save_prices와 get_prices의 공개 계약을 유지한다
Rejected: 호출부에서 종목명을 전달 | 저장 책임이 수집기마다 분산된다
Confidence: high
Scope-risk: narrow
Tested: unittest mapped, unmapped, preservation, viewer filtering"
```

---

### Task 3: Synchronize ticker-map changes atomically into historical prices

**Files:**
- Modify: `stock_db.py:213-317`
- Test: `tests/test_stock_db.py:13-24` and `tests/test_stock_db.py`

**Interfaces:**
- Consumes: `StockDB._sync_daily_price_names(con) -> None`, `load_ticker_map_file(path)`, and `refresh_ticker_map(krx_module)`.
- Produces: atomic ticker-map/name synchronization for JSON imports and KRX refreshes; unchanged mapping return dictionaries.

- [ ] **Step 1: Add a deterministic KRX test double**

Add this class after `NeverCalledKrx` in `tests/test_stock_db.py`:

```python
class NamedKrx:
    def __init__(self, name):
        self.name = name

    def get_market_ticker_list(self, _date, market):
        return ["005930"] if market == "KOSPI" else []

    def get_market_ticker_name(self, ticker):
        if ticker != "005930":
            raise AssertionError(f"예상하지 못한 티커: {ticker}")
        return self.name
```

- [ ] **Step 2: Write failing backfill, rename, refresh, and rollback tests**

Add these methods to `StockDbCacheTest`:

```python
    def test_ticker_map_file_backfills_and_renames_daily_price_names(self):
        self.db.save_prices("005930", [{
            "date": "2026-01-05",
            "open": 70000,
            "high": 71000,
            "low": 69500,
            "close": 70500,
            "volume": 1000,
        }])
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False
        )
        try:
            json.dump({"삼성전자": "005930"}, handle, ensure_ascii=False)
            handle.close()
            self.db.load_ticker_map_file(handle.name)

            with open(handle.name, "w", encoding="utf-8") as file:
                json.dump(
                    {"삼성전자우선": "005930"}, file, ensure_ascii=False
                )
            self.db.load_ticker_map_file(handle.name)

            page = self.db.query_table(
                "daily_prices", filter_col="ticker", filter_val="005930"
            )
            self.assertEqual(page["rows"][0]["name"], "삼성전자우선")
        finally:
            if not handle.closed:
                handle.close()
            os.unlink(handle.name)

    def test_refresh_ticker_map_updates_existing_daily_price_name(self):
        connection = self.db._connect()
        try:
            connection.execute(
                "INSERT INTO ticker_map (ticker, name) VALUES (?, ?)",
                ["005930", "기존이름"],
            )
        finally:
            connection.close()
        self.db.save_prices("005930", [{
            "date": "2026-01-05",
            "open": 70000,
            "high": 71000,
            "low": 69500,
            "close": 70500,
            "volume": 1000,
        }])

        self.db.refresh_ticker_map(NamedKrx("변경이름"))

        connection = self.db._connect()
        try:
            row = connection.execute("""
                SELECT dp.name, tm.name
                FROM daily_prices dp
                JOIN ticker_map tm ON dp.ticker = tm.ticker
                WHERE dp.ticker = ?
            """, ["005930"]).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("변경이름", "변경이름"))

    def test_ticker_map_load_rolls_back_when_name_sync_fails(self):
        connection = self.db._connect()
        try:
            connection.execute(
                "INSERT INTO ticker_map (ticker, name) VALUES (?, ?)",
                ["005930", "기존이름"],
            )
        finally:
            connection.close()
        self.db.save_prices("005930", [{
            "date": "2026-01-05",
            "open": 70000,
            "high": 71000,
            "low": 69500,
            "close": 70500,
            "volume": 1000,
        }])

        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False
        )
        try:
            json.dump({"변경이름": "005930"}, handle, ensure_ascii=False)
            handle.close()
            with patch.object(
                self.db,
                "_sync_daily_price_names",
                side_effect=RuntimeError("name sync failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "name sync failed"):
                    self.db.load_ticker_map_file(handle.name)

            connection = self.db._connect()
            try:
                row = connection.execute("""
                    SELECT dp.name, tm.name
                    FROM daily_prices dp
                    JOIN ticker_map tm ON dp.ticker = tm.ticker
                    WHERE dp.ticker = ?
                """, ["005930"]).fetchone()
            finally:
                connection.close()
            self.assertEqual(row, ("기존이름", "기존이름"))
        finally:
            if not handle.closed:
                handle.close()
            os.unlink(handle.name)
```

- [ ] **Step 3: Run the synchronization tests and verify they fail**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db.StockDbCacheTest.test_ticker_map_file_backfills_and_renames_daily_price_names tests.test_stock_db.StockDbCacheTest.test_refresh_ticker_map_updates_existing_daily_price_name tests.test_stock_db.StockDbCacheTest.test_ticker_map_load_rolls_back_when_name_sync_fails -v
```

Expected: file and KRX rename assertions fail because price names are not synchronized, and the rollback test fails because the synchronization hook is not called.

- [ ] **Step 4: Make file-based ticker-map updates transactional**

Replace the connection block in `load_ticker_map_file()` with:

```python
        con = self._connect()
        transaction_started = False
        try:
            con.execute("BEGIN TRANSACTION")
            transaction_started = True
            con.executemany("""
                INSERT INTO ticker_map (ticker, name, market, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (ticker) DO UPDATE SET
                    name = EXCLUDED.name,
                    updated_at = EXCLUDED.updated_at
            """, rows)
            self._sync_daily_price_names(con)
            con.execute("COMMIT")
            transaction_started = False
        except Exception:
            if transaction_started:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()
```

- [ ] **Step 5: Make KRX ticker-map refreshes transactional**

Replace the connection block inside `if rows:` in `refresh_ticker_map()` with:

```python
            con = self._connect()
            transaction_started = False
            try:
                con.execute("BEGIN TRANSACTION")
                transaction_started = True
                con.executemany("""
                    INSERT INTO ticker_map (ticker, name, market, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (ticker) DO UPDATE SET
                        name = EXCLUDED.name,
                        market = EXCLUDED.market,
                        updated_at = EXCLUDED.updated_at
                """, rows)
                self._sync_daily_price_names(con)
                con.execute("COMMIT")
                transaction_started = False
                logger.info(f"종목 매핑 갱신: {len(rows)}개")
            except Exception:
                if transaction_started:
                    con.execute("ROLLBACK")
                raise
            finally:
                con.close()
```

- [ ] **Step 6: Re-run synchronization and complete stock DB tests**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db.StockDbCacheTest.test_ticker_map_file_backfills_and_renames_daily_price_names tests.test_stock_db.StockDbCacheTest.test_refresh_ticker_map_updates_existing_daily_price_name tests.test_stock_db.StockDbCacheTest.test_ticker_map_load_rolls_back_when_name_sync_fails -v
```

Expected: 3 tests pass.

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db -v
```

Expected: the complete `tests.test_stock_db` suite passes.

- [ ] **Step 7: Commit the synchronization checkpoint**

```bash
git add stock_db.py tests/test_stock_db.py
git commit -m "종목명 변경이 모든 가격 이력에 일관되게 반영되게 한다" -m "Constraint: ticker_map을 종목명의 기준 데이터로 유지한다
Rejected: 가격 행을 수집 시점 이름으로 고정 | 현재 종목명 식별 요구와 충돌한다
Confidence: high
Scope-risk: moderate
Directive: 매핑 업서트와 daily_prices 이름 동기화는 같은 트랜잭션으로 유지한다
Tested: unittest file import, KRX refresh, rollback, stock_db suite"
```

---

### Task 4: Document, migrate, and verify the complete feature

**Files:**
- Modify: `README.md:190-210`
- Verify: `stock_db.py`, `tests/test_stock_db.py`, local ignored `stock_data.duckdb`

**Interfaces:**
- Consumes: completed `StockDB` migration and synchronization behavior from Tasks 1-3.
- Produces: updated operator documentation, migrated local DuckDB, full validation evidence, and synchronized Git history.

- [ ] **Step 1: Update the DuckDB table documentation**

Change the `daily_prices` row in README's DuckDB table to:

```markdown
| `daily_prices` | 종목코드·종목명별 일봉 OHLCV |
```

Add this paragraph immediately below the table:

```markdown
`daily_prices.name`은 `ticker_map.name`을 기준으로 자동 동기화됩니다. 기존 DB는 앱 시작 시 종목명이 소급 반영되고, 매핑이 없는 티커만 `NULL`로 유지되며 이후 매핑 적재 시 자동으로 보완됩니다.
```

- [ ] **Step 2: Run the focused stock DB regression suite**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db -v
```

Expected: every stock DB test passes.

- [ ] **Step 3: Run the complete project test suite**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -v
```

Expected: all tests pass with no errors or failures.

- [ ] **Step 4: Run compile and lint checks**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m py_compile app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py tests/test_stock_db.py
```

Expected: exit code 0 with no output.

Run:

```bash
uvx ruff check app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py tests
```

Expected: `All checks passed!`.

- [ ] **Step 5: Migrate the real local DuckDB through the production initializer**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -c 'from stock_db import StockDB; StockDB("stock_data.duckdb")'
```

Expected: exit code 0; `stock_data.duckdb` remains ignored by Git.

- [ ] **Step 6: Verify real data coverage and consistency read-only**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -c 'import duckdb; c=duckdb.connect("stock_data.duckdb", read_only=True); print("schema", [(r[1], r[2]) for r in c.execute("PRAGMA table_info(\047daily_prices\047)").fetchall()]); print("counts", c.execute("SELECT COUNT(*) AS total, COUNT(*) FILTER (WHERE name IS NULL) AS null_names FROM daily_prices").fetchone()); print("mapped_missing", c.execute("SELECT COUNT(*) FROM daily_prices dp JOIN ticker_map tm ON dp.ticker=tm.ticker WHERE dp.name IS NULL").fetchone()[0]); print("mismatched", c.execute("SELECT COUNT(*) FROM daily_prices dp JOIN ticker_map tm ON dp.ticker=tm.ticker WHERE dp.name IS DISTINCT FROM tm.name").fetchone()[0]); print("unmatched_tickers", c.execute("SELECT COUNT(DISTINCT dp.ticker) FROM daily_prices dp LEFT JOIN ticker_map tm ON dp.ticker=tm.ticker WHERE tm.ticker IS NULL").fetchone()[0]); c.close()'
```

Expected for the current local database:

```text
schema [('ticker', 'VARCHAR'), ('date', 'DATE'), ('open', 'DOUBLE'), ('high', 'DOUBLE'), ('low', 'DOUBLE'), ('close', 'DOUBLE'), ('volume', 'BIGINT'), ('name', 'VARCHAR')]
counts (27700, 0)
mapped_missing 0
mismatched 0
unmatched_tickers 0
```

- [ ] **Step 7: Check the final diff and commit documentation**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intended tracked files plus unrelated untracked `get-pip.py` are shown.

Commit README without staging the ignored DuckDB or `get-pip.py`:

```bash
git add README.md
git commit -m "가격 데이터의 종목명 저장 기준을 운영 문서에 남긴다" -m "Constraint: 기존 DB는 시작 시 자동 마이그레이션된다
Confidence: high
Scope-risk: narrow
Tested: full unittest suite, py_compile, Ruff, live DuckDB migration and consistency queries"
```

- [ ] **Step 8: Push and confirm repository synchronization**

Run:

```bash
git push origin master
git status --short --branch
```

Expected: `master...origin/master` has no ahead/behind count; only `?? get-pip.py` remains.
