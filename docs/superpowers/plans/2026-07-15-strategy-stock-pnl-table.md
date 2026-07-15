# Strategy Stock P&L Table Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 원가격 기준 종목 성과를 제거하고 실제 청산·보유 거래와 비용을 반영한 전략 종목별 손익을 백테스트 결과, 웹 표, 일일 보고서에 일관되게 제공한다.

**Architecture:** `BacktestEngine`이 `TradeRecord`를 종목별로 집계해 `strategy_stock_performance`를 유일한 전략 종목 손익 계약으로 반환한다. 웹과 일일 보고서는 이 백엔드 결과만 표시하고 원가격 기반 `stock_performance`와 `_calc_stock_performance()`은 제거한다. 결정적 회계 테스트로 누적 총매입금액, 실현·미실현손익, 부분 청산과 포트폴리오 손익 대조를 먼저 고정한 뒤 소비자를 전환한다.

**Tech Stack:** Python 3.11, Flask, 표준 `unittest`, vanilla HTML/CSS/JavaScript, `uv`, Ruff

## Global Constraints

- 종목 전략 손익률은 `(실현손익 + 미실현손익) / 누적 총매입금액 × 100`으로 계산한다.
- 누적 총매입금액은 모든 청산·보유 로트의 `실제 매수 체결가 × 수량 + 매수 수수료` 합계다.
- 청산 로트는 매수·매도 슬리피지, 수수료와 세금이 반영된 `TradeRecord.pnl`을 사용한다.
- 보유 로트는 `마지막 종가 × 수량 - 로트 총매입금액`으로 계산하고 가상의 매도 비용은 차감하지 않는다.
- 가격만 적재되고 거래가 없는 종목은 전략 종목 손익 결과에 포함하지 않는다.
- 열린 거래의 마지막 가격 데이터가 없으면 종목코드를 포함한 `ValueError`를 발생시킨다.
- 결과 필드는 `strategy_stock_performance`이며 기존 `stock_performance` 호환 별칭은 남기지 않는다.
- 결과 행은 `total_pnl` 내림차순, 동률이면 `ticker` 오름차순으로 정렬한다.
- 금액은 원 단위, 손익률은 소수점 둘째 자리로 응답 직전에 반올림한다.
- 웹 표 제목은 `전략 종목별 손익`이며 시작가, 종료가와 종목 원가격 MDD를 표시하지 않는다.
- `trades`, `trades_by_stock`, CSV 스키마, 포트폴리오 메트릭과 전략 매매 로직은 변경하지 않는다.
- 외부 의존성을 추가하지 않고 추적되지 않은 `get-pip.py`는 수정하거나 커밋하지 않는다.

## File Map

- `backtester.py`: 거래 로트를 종목별 전략 손익으로 집계하고 결과 계약을 교체한다.
- `tests/test_backtester.py`: 비용 포함 혼합 거래, 부분 청산, 미거래 종목 제외, 가격 데이터 불변식과 전체 손익 대조를 검증한다.
- `app.py`: 원가격 표를 전략 종목별 손익 표와 새 렌더러로 교체한다.
- `tests/test_app.py`: 새 표 제목·열·결과 키·부호 표시 계약과 기존 바인딩 제거를 검증한다.
- `daily_report.py`: 텔레그램의 원가격 종목 수익률을 전략 종목별 총손익으로 교체한다.
- `tests/test_daily_report.py`: 총손익 정렬·표시, 0 부호, HTML 이스케이프와 원가격 MDD 제거를 검증한다.

---

### Task 1: 거래 기록 기반 전략 종목 손익 결과 추가

**Files:**
- Modify: `backtester.py:867-897`
- Replace: `backtester.py:1162-1190`
- Test: `tests/test_backtester.py:16-64`

**Interfaces:**
- Consumes: `Portfolio.trades: List[TradeRecord]`, `TradeRecord.exec_price`, `TradeRecord.entry_cost`, `TradeRecord.pnl`, `BacktestEngine.price_data`
- Produces: `BacktestEngine._calc_strategy_stock_performance() -> List[dict]`, 결과 키 `strategy_stock_performance`

- [ ] **Step 1: 전략 손익 회계와 결과 계약의 실패 테스트 작성**

`tests/test_backtester.py`의 `BacktestAccountingTest`에 다음 메서드를 추가한다.

```python
    def test_strategy_stock_performance_combines_realized_and_open_pnl(self):
        engine = BacktestEngine(initial_capital=2_000, commission_pct=1.0)
        engine.add_price_data(
            "AAA",
            [
                price("2026-01-02", 100),
                price("2026-01-05", 110),
                price("2026-01-06", 100),
                price("2026-01-07", 120),
            ],
            name="전략종목",
        )
        engine.add_price_data(
            "BBB",
            [price("2026-01-02", 50), price("2026-01-07", 40)],
            name="미거래종목",
        )

        engine.portfolio.buy("AAA", 100, 10, "2026-01-02", "전략종목")
        engine.portfolio.sell("AAA", 110, 10, "2026-01-05")
        engine.portfolio.buy("AAA", 100, 5, "2026-01-06", "전략종목")
        engine.portfolio.snapshot("2026-01-07", {"AAA": 120, "BBB": 40})

        results = engine.get_results()

        self.assertNotIn("stock_performance", results)
        self.assertEqual(
            results["strategy_stock_performance"],
            [
                {
                    "ticker": "AAA",
                    "name": "전략종목",
                    "trade_count": 2,
                    "closed_count": 1,
                    "open_count": 1,
                    "total_buy_amount": 1_515,
                    "realized_pnl": 79,
                    "unrealized_pnl": 95,
                    "total_pnl": 174,
                    "return_pct": 11.49,
                }
            ],
        )
        self.assertEqual(results["metrics"]["profit_loss"], 174)
        self.assertEqual(
            sum(row["total_pnl"] for row in results["strategy_stock_performance"]),
            results["metrics"]["profit_loss"],
        )

    def test_strategy_stock_performance_reconciles_partial_sale_lots(self):
        engine = BacktestEngine(initial_capital=10_000, commission_pct=1.0)
        engine.add_price_data(
            "AAA",
            [price("2026-01-02", 100), price("2026-01-07", 120)],
            name="부분청산",
        )
        engine.portfolio.buy("AAA", 100, 10, "2026-01-02", "부분청산")
        engine.portfolio.buy("AAA", 100, 10, "2026-01-05", "부분청산")
        engine.portfolio.sell("AAA", 110, 15, "2026-01-06")
        engine.portfolio.snapshot("2026-01-07", {"AAA": 120})

        results = engine.get_results()
        row = results["strategy_stock_performance"][0]

        self.assertEqual(row["trade_count"], 3)
        self.assertEqual(row["closed_count"], 2)
        self.assertEqual(row["open_count"], 1)
        self.assertEqual(row["total_buy_amount"], 2_020)
        self.assertEqual(row["realized_pnl"], 118)
        self.assertEqual(row["unrealized_pnl"], 95)
        self.assertEqual(row["total_pnl"], 214)
        self.assertEqual(row["return_pct"], 10.57)
        self.assertEqual(row["total_pnl"], results["metrics"]["profit_loss"])

    def test_strategy_stock_performance_rejects_open_trade_without_price(self):
        engine = BacktestEngine(initial_capital=1_000)
        engine.portfolio.buy("AAA", 100, 1, "2026-01-02", "가격없음")
        engine.portfolio.snapshot("2026-01-02", {"AAA": 100})

        with self.assertRaisesRegex(ValueError, "AAA.*가격 데이터"):
            engine.get_results()
```

- [ ] **Step 2: 기존 원가격 결과로 인해 테스트가 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_backtester.BacktestAccountingTest.test_strategy_stock_performance_combines_realized_and_open_pnl tests.test_backtester.BacktestAccountingTest.test_strategy_stock_performance_reconciles_partial_sale_lots tests.test_backtester.BacktestAccountingTest.test_strategy_stock_performance_rejects_open_trade_without_price -v`

Expected: 첫 두 테스트는 `strategy_stock_performance` 키 부재로 실패하고 가격 불변식 테스트는 `ValueError`가 발생하지 않아 실패한다.

- [ ] **Step 3: 결과 키를 새 전략 손익 집계로 교체**

`backtester.py`의 `get_results()` 반환 딕셔너리에서 다음 항목을 교체한다.

```python
            'strategy_stock_performance': (
                self._calc_strategy_stock_performance()
            ),
```

기존 `_calc_stock_performance()` 전체를 다음 메서드로 교체한다.

```python
    def _calc_strategy_stock_performance(self) -> List[dict]:
        """실제 거래 로트를 종목별 전략 손익으로 집계한다."""
        grouped: Dict[str, dict] = {}

        for trade in self.portfolio.trades:
            row = grouped.setdefault(
                trade.ticker,
                {
                    'ticker': trade.ticker,
                    'name': trade.name,
                    'trade_count': 0,
                    'closed_count': 0,
                    'open_count': 0,
                    'total_buy_amount': 0.0,
                    'realized_pnl': 0.0,
                    'unrealized_pnl': 0.0,
                },
            )
            buy_cost = trade.exec_price * trade.shares + trade.entry_cost
            row['trade_count'] += 1
            row['total_buy_amount'] += buy_cost

            if trade.status == 'closed':
                row['closed_count'] += 1
                row['realized_pnl'] += trade.pnl
                continue

            price_rows = self.price_data.get(trade.ticker)
            if not price_rows:
                raise ValueError(
                    f"열린 거래 종목 {trade.ticker}의 마지막 가격 데이터가 없습니다."
                )
            row['open_count'] += 1
            last_close = price_rows[-1]['close']
            row['unrealized_pnl'] += last_close * trade.shares - buy_cost

        performance = []
        for row in grouped.values():
            total_pnl = row['realized_pnl'] + row['unrealized_pnl']
            total_buy_amount = row['total_buy_amount']
            return_pct = (
                total_pnl / total_buy_amount * 100
                if total_buy_amount > 0 else 0.0
            )
            performance.append({
                'ticker': row['ticker'],
                'name': row['name'],
                'trade_count': row['trade_count'],
                'closed_count': row['closed_count'],
                'open_count': row['open_count'],
                'total_buy_amount': round(total_buy_amount),
                'realized_pnl': round(row['realized_pnl']),
                'unrealized_pnl': round(row['unrealized_pnl']),
                'total_pnl': round(total_pnl),
                'return_pct': round(return_pct, 2),
            })

        performance.sort(key=lambda item: (-item['total_pnl'], item['ticker']))
        return performance
```

- [ ] **Step 4: 엔진 회계 테스트와 전체 엔진 테스트 통과 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_backtester -v`

Expected: 모든 `tests.test_backtester` 테스트가 통과한다.

- [ ] **Step 5: 엔진 계약 변경 커밋**

```bash
git add backtester.py tests/test_backtester.py
git commit -m "전략이 만든 종목 손익만 결과로 신뢰할 수 있게 한다" -m "Constraint: Return is total realized plus unrealized P&L over cumulative buy cost
Rejected: Keep raw stock_performance as an alias | It preserves the misleading contract
Confidence: high
Scope-risk: moderate
Directive: Reconcile per-stock P&L against final portfolio equity whenever accounting changes
Tested: unittest tests.test_backtester
Not-tested: Browser rendering and live cached-data backtest"
```

---

### Task 2: 웹 원가격 표를 전략 종목별 손익 표로 교체

**Files:**
- Modify: `app.py:1199-1212`
- Modify: `app.py:1358-1400`
- Replace: `app.py:1484-1499`
- Test: `tests/test_app.py:316-356`

**Interfaces:**
- Consumes: 결과 배열 `strategy_stock_performance`
- Produces: DOM 본문 `strategyStockBody`, JavaScript `renderStrategyStockTable(results)`

- [ ] **Step 1: 새 표 계약과 기존 바인딩 제거의 실패 테스트 작성**

`tests/test_app.py`의 `FlaskApiTest`에 다음 메서드를 추가한다.

```python
    def test_backtest_page_renders_strategy_stock_pnl_table(self):
        template = app_module.BACKTEST_TEMPLATE

        self.assertIn("<h3>전략 종목별 손익</h3>", template)
        for heading in (
            "종목명", "종목코드", "거래건수", "청산건수", "보유건수",
            "누적 총매입금액", "실현손익", "평가손익", "총손익", "손익률",
        ):
            self.assertIn(f">{heading}</th>", template)
        self.assertIn('id="strategyStockBody"', template)
        self.assertIn("renderStrategyStockTable(r);", template)
        self.assertIn("(r.strategy_stock_performance || [])", template)
        self.assertIn(
            "value > 0 ? 'pos-text' : value < 0 ? 'neg-text' : ''",
            template,
        )
        self.assertIn("value > 0 ? '+' : ''", template)
        self.assertNotIn("<h3>종목별 성과</h3>", template)
        self.assertNotIn("(r.stock_performance || [])", template)
        self.assertNotIn('<th class="r">시작가</th>', template)
        self.assertNotIn('<th class="r">종료가</th>', template)
```

- [ ] **Step 2: 기존 템플릿이 새 제목·열·결과 키를 제공하지 않아 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.FlaskApiTest.test_backtest_page_renders_strategy_stock_pnl_table -v`

Expected: `전략 종목별 손익` 제목과 `strategy_stock_performance` 바인딩이 없어 실패한다.

- [ ] **Step 3: 표 마크업과 렌더러를 새 결과 계약으로 교체**

`app.py`의 기존 `종목별 성과` 표를 다음 마크업으로 교체한다.

```html
        <div class="tbl-box">
            <h3>전략 종목별 손익</h3>
            <table>
                <thead><tr>
                    <th>종목명</th><th>종목코드</th>
                    <th class="r">거래건수</th><th class="r">청산건수</th>
                    <th class="r">보유건수</th>
                    <th class="r">누적 총매입금액</th>
                    <th class="r">실현손익</th><th class="r">평가손익</th>
                    <th class="r">총손익</th><th class="r">손익률</th>
                </tr></thead>
                <tbody id="strategyStockBody"></tbody>
            </table>
        </div>
```

`renderResults()`의 호출을 다음으로 바꾼다.

```javascript
    renderStrategyStockTable(r);
```

기존 `renderStockTable()` 전체를 다음 함수로 교체한다.

```javascript
function renderStrategyStockTable(r) {
    const body = document.getElementById('strategyStockBody');
    body.innerHTML = '';
    const pnlClass = value => value > 0 ? 'pos-text' : value < 0 ? 'neg-text' : '';
    const signed = (value, suffix = '') => {
        const sign = value > 0 ? '+' : '';
        return `${sign}${fmt(value)}${suffix}`;
    };
    (r.strategy_stock_performance || []).forEach(s => {
        body.innerHTML += `<tr>
            <td><b>${s.name}</b></td>
            <td class="c">${s.ticker}</td>
            <td class="r">${fmt(s.trade_count)}</td>
            <td class="r">${fmt(s.closed_count)}</td>
            <td class="r">${fmt(s.open_count)}</td>
            <td class="r">${fmt(s.total_buy_amount)}</td>
            <td class="r ${pnlClass(s.realized_pnl)}">${signed(s.realized_pnl)}</td>
            <td class="r ${pnlClass(s.unrealized_pnl)}">${signed(s.unrealized_pnl)}</td>
            <td class="r ${pnlClass(s.total_pnl)}">${signed(s.total_pnl)}</td>
            <td class="r ${pnlClass(s.return_pct)}">${signed(s.return_pct, '%')}</td>
        </tr>`;
    });
}
```

- [ ] **Step 4: 템플릿 계약과 전체 Flask 테스트 통과 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app -v`

Expected: 모든 `tests.test_app` 테스트가 통과한다.

- [ ] **Step 5: 웹 소비자 전환 커밋**

```bash
git add app.py tests/test_app.py
git commit -m "백테스트 표가 실제 전략 손익만 설명하게 한다" -m "Constraint: Remove start price, end price, and raw-stock MDD from the result table
Rejected: Aggregate trades in JavaScript | It would duplicate accounting outside the engine
Confidence: high
Scope-risk: narrow
Directive: Keep web rendering bound to strategy_stock_performance
Tested: unittest tests.test_app
Not-tested: Live browser DOM after server restart"
```

---

### Task 3: 일일 보고서를 같은 전략 손익 계약으로 전환

**Files:**
- Modify: `daily_report.py:263-278`
- Test: `tests/test_daily_report.py:20-61`

**Interfaces:**
- Consumes: 결과 배열 `strategy_stock_performance`
- Produces: 텔레그램 HTML 섹션 `▸ 전략 종목별 손익`

- [ ] **Step 1: 전략 총손익 표시·정렬·이스케이프의 실패 테스트 작성**

`tests/test_daily_report.py`의 두 기존 메시지 픽스처에서 `"stock_performance": []`를 `"strategy_stock_performance": []`로 바꾸고, `DailyReportSourceValidationTest`에 다음 메서드를 추가한다.

```python
    def test_message_uses_strategy_stock_pnl_instead_of_raw_performance(self):
        message = daily_report.format_telegram_message(
            [],
            {},
            {
                "metrics": {},
                "strategy_stock_performance": [
                    {
                        "name": "손실종목",
                        "total_pnl": -500,
                        "return_pct": -2.0,
                    },
                    {
                        "name": "<b>수익종목</b>",
                        "total_pnl": 1_234,
                        "return_pct": 5.5,
                    },
                    {
                        "name": "보합종목",
                        "total_pnl": 0,
                        "return_pct": 0.0,
                    },
                ],
            },
            {},
        )

        self.assertIn("<b>▸ 전략 종목별 손익</b>", message)
        self.assertIn("📈 &lt;b&gt;수익종목&lt;/b&gt;: +1,234원 (+5.50%)", message)
        self.assertIn("📉 손실종목: -500원 (-2.00%)", message)
        self.assertIn("📈 보합종목: 0원 (0.00%)", message)
        self.assertLess(message.index("수익종목"), message.index("손실종목"))
        self.assertNotIn("▸ 개별 종목 수익률", message)
        self.assertNotIn("(MDD", message)
```

- [ ] **Step 2: 기존 원가격 메시지 구현 때문에 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_daily_report.DailyReportSourceValidationTest.test_message_uses_strategy_stock_pnl_instead_of_raw_performance -v`

Expected: 새 결과 키를 읽지 않아 `전략 종목별 손익` 섹션이 생성되지 않고 실패한다.

- [ ] **Step 3: 원가격 종목 수익률 블록을 전략 손익 블록으로 교체**

`daily_report.py`의 `# 개별 종목 성과` 블록 전체를 다음 코드로 교체한다.

```python
    # 전략 종목별 손익
    strategy_perf = bt_results.get('strategy_stock_performance', [])
    if strategy_perf:
        lines.append("")
        lines.append("<b>▸ 전략 종목별 손익</b>")
        sorted_perf = sorted(
            strategy_perf,
            key=lambda row: row.get('total_pnl', 0),
            reverse=True,
        )
        for stock in sorted_perf[:10]:
            total_pnl = stock.get('total_pnl', 0)
            return_pct = stock.get('return_pct', 0)
            icon = "📈" if total_pnl >= 0 else "📉"
            pnl_sign = "+" if total_pnl > 0 else ""
            return_sign = "+" if return_pct > 0 else ""
            name = escape(str(stock['name']))
            lines.append(
                f"  {icon} {name}: {pnl_sign}{total_pnl:,.0f}원 "
                f"({return_sign}{return_pct:.2f}%)"
            )
```

- [ ] **Step 4: 일일 보고서 테스트 통과 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_daily_report -v`

Expected: 모든 `tests.test_daily_report` 테스트가 통과한다.

- [ ] **Step 5: 일일 보고서 소비자 전환 커밋**

```bash
git add daily_report.py tests/test_daily_report.py
git commit -m "자동 보고서도 전략이 만든 종목 손익을 전달하게 한다" -m "Constraint: Rank report rows by total strategy P&L and retain HTML escaping
Rejected: Keep the raw-price section beside strategy P&L | It would preserve the original ambiguity
Confidence: high
Scope-risk: narrow
Directive: Use strategy_stock_performance for every per-stock strategy summary
Tested: unittest tests.test_daily_report
Not-tested: Telegram network delivery"
```

---

### Task 4: 전체 회귀와 실제 백테스트 결과 검증

**Files:**
- Verify: `backtester.py`
- Verify: `app.py`
- Verify: `daily_report.py`
- Verify: `tests/test_backtester.py`
- Verify: `tests/test_app.py`
- Verify: `tests/test_daily_report.py`

**Interfaces:**
- Consumes: 완료된 엔진·웹·보고서 계약
- Produces: 전체 테스트, 정적 검사, 실행 중인 Flask 화면과 실제 캐시 데이터 결과에 대한 완료 증거

- [ ] **Step 1: 변경 영역 통합 테스트 실행**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_backtester tests.test_app tests.test_daily_report -v`

Expected: 세 모듈의 모든 테스트가 통과한다.

- [ ] **Step 2: 전체 회귀 테스트 실행**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -v`

Expected: 전체 테스트가 `OK`로 종료한다.

- [ ] **Step 3: 구문·정적 검사·공백 오류 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m py_compile app.py backtester.py daily_report.py tests/test_app.py tests/test_backtester.py tests/test_daily_report.py`

Expected: 출력 없이 종료 코드 `0`이다.

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt ruff check app.py backtester.py daily_report.py tests/test_app.py tests/test_backtester.py tests/test_daily_report.py`

Expected: `All checks passed!`가 출력된다.

Run: `git diff --check HEAD~3..HEAD`

Expected: 출력 없이 종료 코드 `0`이다.

- [ ] **Step 4: Flask 앱을 새 코드로 재시작하고 화면 계약 확인**

Run:

```bash
for pid in $(lsof -ti tcp:5000); do
  kill "$pid"
done
nohup uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python app.py >/tmp/gukjang-gumsak-app.log 2>&1 &
for attempt in {1..30}; do
  if curl -fsS http://127.0.0.1:5000/backtest >/dev/null; then
    break
  fi
  sleep 1
done
```

Expected: 새 Flask 프로세스가 `127.0.0.1:5000`에서 시작한다.

Run: `curl -fsS http://127.0.0.1:5000/backtest | rg "전략 종목별 손익|strategyStockBody|strategy_stock_performance"`

Expected: 세 새 계약 문자열이 출력되고 `curl`이 성공한다.

Run: `curl -fsS http://127.0.0.1:5000/backtest | rg "<h3>종목별 성과</h3>|<th class=\"r\">시작가</th>|r.stock_performance"`

Expected: 일치 항목이 없어 `rg`가 종료 코드 `1`로 끝난다.

- [ ] **Step 5: 현재 캐시의 국민연금 단독 1점 종목으로 실제 백테스트 실행**

Run:

```bash
curl -fsS -X POST http://127.0.0.1:5000/api/backtest/run \
  -H 'Content-Type: application/json' \
  -d '{"period":3,"capital":100000000,"strategy":"vol_trailing_stop_loss","stop_loss":7,"scores":[1],"items":["nps"]}'
```

Expected: `{"status":"started"...}`가 반환된다.

완료될 때까지 10초 이하 간격으로 다음 명령을 반복한다.

Run: `curl -fsS http://127.0.0.1:5000/api/backtest/status -o /tmp/gukjang-backtest-status.json && uv run --isolated --managed-python --python 3.11 python -c 'import json; data=json.load(open("/tmp/gukjang-backtest-status.json")); print(data["status"], data.get("progress", ""), data.get("error_msg", ""))'`

Expected: 최종 상태가 `done`이며 `error_msg`가 비어 있다.

- [ ] **Step 6: 실제 결과 키·손익 대조·원가격 필드 제거 확인**

Run:

```bash
uv run --isolated --managed-python --python 3.11 python -c '
import json
data = json.load(open("/tmp/gukjang-backtest-status.json"))
results = data["results"]
rows = results["strategy_stock_performance"]
assert "stock_performance" not in results
assert rows
assert all(row["trade_count"] == row["closed_count"] + row["open_count"] for row in rows)
assert all(set(row) == {"ticker", "name", "trade_count", "closed_count", "open_count", "total_buy_amount", "realized_pnl", "unrealized_pnl", "total_pnl", "return_pct"} for row in rows)
delta = abs(sum(row["total_pnl"] for row in rows) - results["metrics"]["profit_loss"])
assert delta <= max(1, len(rows)), (delta, len(rows))
print({"stocks": len(rows), "profit_loss": results["metrics"]["profit_loss"], "row_pnl_sum": sum(row["total_pnl"] for row in rows), "rounding_delta": delta})
'
```

Expected: 검증 실패 없이 종목 수, 전체 손익, 행 손익 합계와 허용오차가 출력된다.

- [ ] **Step 7: 최종 저장소 상태와 커밋 범위 확인**

Run: `git status --short --branch && git log --oneline -6`

Expected: `get-pip.py`만 추적되지 않은 상태로 남고 엔진, 웹, 보고서 변경은 각각 Lore 형식 커밋으로 기록되어 있다.
