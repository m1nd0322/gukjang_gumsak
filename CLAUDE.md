# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Korean stock market screening system (국장검색) that scores stocks based on three criteria from FnGuide, then optionally backtests high-scoring stocks using historical price data.

Language: Korean (UI, variable names, comments). All user-facing text is in Korean.

## Running the Application

```bash
pip install -r requirements.txt
python app.py
# Opens at http://localhost:5000
# Backtest page at http://localhost:5000/backtest
```

Requires Chrome installed (Selenium headless crawling). The standalone CLI version (`stock_screener.py`) uses `requests` instead of Selenium but is not integrated with the web server.

## Architecture

### Data Flow

```
FnGuide (3 pages) --[Selenium crawl]--> Raw tables --[score]--> Ranked stocks
                                                                      |
                                              Stocks scoring >= 2 ----+
                                                                      |
                              pykrx --[incremental]--> DuckDB ---> BacktestEngine ---> Results JSON
```

### Module Responsibilities

- **`app.py`** — Flask web server. Contains all routes, Selenium crawling (`fetch_all_data`), scoring logic (`calculate_scores`), backtest orchestration, APScheduler (daily 8AM refresh), and two inline HTML templates (`HTML_TEMPLATE`, `BACKTEST_TEMPLATE`). Global state is managed via `current_data` / `backtest_state` dicts protected by threading locks.

- **`backtester.py`** — Standalone backtest engine with no external backtest dependencies. Key classes:
  - `CostConfig` — slippage, commission, tax rates
  - `Portfolio` — cash/position management, buy/sell with cost modeling
  - `BacktestEngine` — 6 strategies: `run_equal_weight`, `run_rebalance`, `run_custom`, `run_volatility_trailing_stop`, `run_ma_filter`, `run_composite`

- **`stock_db.py`** — `StockDB` class wrapping DuckDB. Three tables: `daily_prices`, `ticker_map`, `index_prices`. Handles incremental data fetching (only fetches dates not already in DB). Each method creates a new DuckDB connection for thread safety.

- **`stock_screener.py`** — Original standalone CLI script (uses `requests` + `pandas`). Generates a static HTML file. Not used by the web server.

### Three Screening Criteria (from FnGuide)

| Criterion | FnGuide Page | Code Label |
|-----------|-------------|------------|
| Annual earnings turnaround (연간실적호전) | `ScreenerBasics_turn.asp` → `#grid_A` | `turn` |
| Foreign/institutional net buying reversal (순매수전환) | `SupplyTrend.asp` → `#tbl_2` | `supply` |
| National Pension Service holdings (국민연금 보유) | `inst.asp` → `table.ctb1` | `nps` |

Each criterion = 1 point. Stocks are ranked by total score (max 3).

### Key API Routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/` | GET | Main screening dashboard |
| `/backtest` | GET | Backtest page |
| `/api/refresh` | POST | Trigger async data refresh (Selenium crawl) |
| `/api/status` | GET | Current screening data + status |
| `/api/backtest/run` | POST | Start backtest (params: period, capital, strategy, slippage, commission, tax) |
| `/api/backtest/status` | GET | Backtest progress/results |
| `/api/backtest/csv` | GET | Download backtest results as CSV |

### Threading Model

Both refresh and backtest run in daemon threads. Status is polled from the frontend via setInterval. Shared state is guarded by `data_lock` (screening) and `bt_lock` (backtest).

### Data Storage

- **`cache_data.json`** — Cached screening results (survives server restarts)
- **`stock_data.duckdb`** — Historical price data, ticker mapping, KOSPI index data. Incremental: only fetches new dates from pykrx API with 0.3s delay between calls

### Frontend

HTML templates are embedded as Python raw strings in `app.py`. The backtest page uses Chart.js (CDN) for equity curve and drawdown charts. No build step or separate frontend tooling.
