# Trade Return Percent Formatting Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Format backtest trade-history return percentages to exactly two decimal places in both the browser table and downloaded CSV, rounding at the third decimal place without changing numeric backtest results.

**Architecture:** Keep `results.trades[*].return_pct` numeric and untouched. Format only at the two presentation boundaries: `Intl.NumberFormat` in the browser template and a small `Decimal`-based formatter in the CSV route.

**Tech Stack:** Python 3.11, Flask, browser JavaScript, Python `decimal`, standard `unittest`, Ruff

## Global Constraints

- The `return_pct` API field remains numeric and retains its original precision.
- The trade-history browser table always displays exactly two decimal places and a `%` suffix.
- Positive and zero browser values keep the existing `+` prefix; negative values keep `-`; missing values remain `-`.
- CSV return percentages always contain exactly two decimal places without a `+` prefix or `%` suffix; missing values remain empty.
- Rounding uses ordinary half-up behavior at the third decimal place, including negative values.
- Return-based colors, filters, backtest calculations, and other performance tables remain unchanged.
- No new dependency is added and README is not changed.

## File Structure

- Modify `app.py`: format the browser trade-history percentage and CSV trade-history percentage at their existing output boundaries.
- Modify `tests/test_app.py`: lock the browser template contract and exercise the real CSV endpoint with positive, negative, padded, zero, and missing values.

---

### Task 1: Format the browser trade-history percentage

**Files:**
- Modify: `app.py:1560-1600`
- Test: `tests/test_app.py:360-390`

**Interfaces:**
- Consumes: numeric or `null` `t.return_pct` from `results.trades`.
- Produces: `fmtTradePct(value) -> string`, used only by `renderTradeRows()`.

- [ ] **Step 1: Write the failing browser-template test**

Add this method after `test_backtest_page_renders_strategy_stock_pnl_table` in `FlaskApiTest`:

```python
    def test_trade_history_formats_return_pct_to_two_decimal_places(self):
        template = app_module.BACKTEST_TEMPLATE

        self.assertIn("const tradePctFormatter = new Intl.NumberFormat", template)
        self.assertIn("minimumFractionDigits: 2", template)
        self.assertIn("maximumFractionDigits: 2", template)
        self.assertIn("roundingMode: 'halfExpand'", template)
        self.assertIn("tradePctFormatter.format(Math.abs(Number(v)))", template)
        self.assertIn("${fmtTradePct(t.return_pct)}", template)
        self.assertNotIn("((v >= 0 ? '+' : '') + v + '%')", template)
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.FlaskApiTest.test_trade_history_formats_return_pct_to_two_decimal_places -v
```

Expected: FAIL because `BACKTEST_TEMPLATE` does not contain `tradePctFormatter` and still concatenates the raw percentage.

- [ ] **Step 3: Add the minimal browser formatter**

In `BACKTEST_TEMPLATE`, add this formatter immediately before `renderTradeRows()`:

```javascript
const tradePctFormatter = new Intl.NumberFormat('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
    useGrouping: false,
    roundingMode: 'halfExpand',
});

function fmtTradePct(v) {
    if (v == null) return '-';
    const value = Number(v);
    const sign = value >= 0 ? '+' : '-';
    return sign + tradePctFormatter.format(Math.abs(value)) + '%';
}

function renderTradeRows(trades) {
```

Delete the old loop-local raw formatter:

```javascript
        const fmtPct = (v) => v != null ? ((v >= 0 ? '+' : '') + v + '%') : '-';
```

Replace the return-percentage cell with:

```javascript
            <td class="r ${retCls}">${fmtTradePct(t.return_pct)}</td>
```

- [ ] **Step 4: Run focused and Flask tests and verify GREEN**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.FlaskApiTest.test_trade_history_formats_return_pct_to_two_decimal_places -v
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app -v
```

Expected: the focused test and all `tests.test_app` tests PASS.

- [ ] **Step 5: Commit the browser formatter**

```bash
git add app.py tests/test_app.py
git commit -m "매매 상세 수익률을 두 자리로 읽기 쉽게 만든다" \
  -m "화면 출력 경계에서만 half-up 반올림과 두 자리 패딩을 적용한다." \
  -m $'Constraint: return_pct 숫자 계약과 백테스트 계산은 유지한다\nRejected: API 표시 필드 추가 | 화면 한 곳을 위해 계약을 확장할 필요가 없다\nConfidence: high\nScope-risk: narrow\nDirective: 상세 이력 화면의 수익률은 fmtTradePct를 통해 출력한다\nTested: focused browser-template test and tests.test_app\nNot-tested: CSV formatting is implemented in Task 2'
```

---

### Task 2: Format CSV trade-history percentages and run full verification

**Files:**
- Modify: `app.py:12-35,557-620`
- Modify: `tests/test_app.py:1-10,390-450`

**Interfaces:**
- Consumes: `value: int | float | None` from `results.trades[*].return_pct`.
- Produces: `_format_return_pct(value) -> str`; two-decimal half-up text or `''` for `None`.

- [ ] **Step 1: Write the failing CSV endpoint test**

Add `csv` and `io` to the standard-library imports at the top of `tests/test_app.py`:

```python
import csv
import io
import json
```

Add this method after the browser formatting test:

```python
    def test_backtest_csv_formats_trade_return_pct_to_two_decimal_places(self):
        engine = MagicMock()
        engine.get_daily_detail.return_value = []

        def trade_row(ticker, return_pct):
            return {
                "ticker": ticker,
                "name": f"종목{ticker}",
                "entry_date": "2026-01-02",
                "entry_price": 100,
                "shares": 1,
                "buy_amount": 100,
                "avg_price": 100,
                "total_buy_amount": 100,
                "eval_amount": 110,
                "eval_pnl": 10,
                "exit_date": "2026-01-03",
                "exit_price": 110,
                "exit_cost": 0,
                "realized_pnl": 10,
                "return_pct": return_pct,
                "status": "closed",
            }

        return_values = (12.345, -7.105, 1.2, 0, None)
        trades = [
            trade_row(str(index), value)
            for index, value in enumerate(return_values, start=1)
        ]
        with app_module.bt_lock:
            app_module.backtest_state.update(
                engine=engine,
                results={"trades": trades, "config": {}},
            )

        response = self.client.get("/api/backtest/csv")

        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(response.data.decode("utf-8-sig"))))
        section_index = rows.index(["=== 매매 상세 이력 ==="])
        trade_rows = rows[section_index + 2:]
        self.assertEqual(
            [row[14] for row in trade_rows],
            ["12.35", "-7.11", "1.20", "0.00", ""],
        )
        self.assertEqual(
            [trade["return_pct"] for trade in trades],
            list(return_values),
        )
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.FlaskApiTest.test_backtest_csv_formats_trade_return_pct_to_two_decimal_places -v
```

Expected: FAIL because the CSV contains `12.345`, `-7.105`, `1.2`, and `0` instead of fixed two-decimal text.

- [ ] **Step 3: Add the CSV formatter and use it at the output boundary**

Add the decimal imports beside the existing standard-library imports in `app.py`:

```python
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
```

Add the formatter after `BACKTEST_ITEM_SOURCES`:

```python
RETURN_PCT_QUANTUM = Decimal("0.01")


def _format_return_pct(value):
    if value is None:
        return ""
    rounded = Decimal(str(value)).quantize(
        RETURN_PCT_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    return format(rounded, ".2f")
```

In `api_backtest_csv()`, replace the current `return_pct` expression with:

```python
            _format_return_pct(t['return_pct']),
```

Do not mutate `results`, `trades`, or the numeric API field.

- [ ] **Step 4: Run focused and Flask tests and verify GREEN**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.FlaskApiTest.test_backtest_csv_formats_trade_return_pct_to_two_decimal_places -v
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app -v
```

Expected: the focused CSV test and all `tests.test_app` tests PASS.

- [ ] **Step 5: Run full verification**

Run:

```bash
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -v
uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m py_compile *.py tests/*.py
uvx ruff check app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py tests
git diff --check
```

Expected: all tests PASS, Python compilation exits 0, Ruff reports `All checks passed!`, and `git diff --check` produces no output.

- [ ] **Step 6: Review scope and commit**

Run:

```bash
git diff -- app.py tests/test_app.py
git status --short
```

Expected: only `app.py` and `tests/test_app.py` are implementation changes; the approved spec and plan are the only documentation changes.

Commit:

```bash
git add app.py tests/test_app.py
git commit -m "CSV에서도 매매 수익률을 같은 두 자리로 전달한다" \
  -m "Decimal half-up 포맷터를 CSV 출력 경계에 적용하고 숫자형 결과는 보존한다." \
  -m $'Constraint: 화면과 CSV의 두 자리 반올림 결과가 일치해야 한다\nRejected: return_pct 원본 반올림 | 계산 정밀도와 API 계약을 바꾼다\nConfidence: high\nScope-risk: narrow\nDirective: CSV 상세 이력 수익률은 _format_return_pct를 통해 기록한다\nTested: focused CSV test, tests.test_app, full unittest, py_compile, Ruff, git diff --check\nNot-tested: 없음'
```
