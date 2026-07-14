# Volatility Trailing Stop Loss Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 기존 변동성 가중·트레일링 스탑 전략을 그대로 보존하면서 평균 체결가 기준의 사용자 지정 스탑로스를 결합한 여섯 번째 웹 백테스트 전략을 제공한다.

**Architecture:** `BacktestEngine.run_volatility_trailing_stop()`에 선택적 `stop_loss_pct`를 추가해 기존 전략과 새 전략이 하나의 실행 루프를 공유한다. Flask API는 기본값·유한성·범위를 검증하고 백그라운드 작업에 키워드 인자로 전달하며, UI와 결과 설정은 같은 값을 노출한다. 결정적 단위 테스트와 현재 스크리닝 데이터의 국민연금 단독 1점 후보 종단간 실행으로 동작을 검증한다.

**Tech Stack:** Python 3.11, Flask, 표준 `unittest`, `unittest.mock`, DuckDB 가격 캐시, vanilla HTML/CSS/JavaScript, `uv`, Ruff

## Global Constraints

- 새 API 전략 키는 `vol_trailing_stop_loss`, 화면 표시는 `변동성 가중 + 트레일링 스탑 + 스탑로스`다.
- 스탑로스 기본값은 `7.0%`, 허용 범위는 `0.1%` 이상 `50.0%` 이하, UI 입력 간격은 `0.5%`다.
- 스탑로스는 매수 슬리피지를 포함한 `Portfolio.positions[ticker]['avg_price']` 대비 당일 종가 수익률로 판정한다.
- 새 전략의 기존 위험 설정은 최고 종가 대비 `-10%` 트레일링 스탑, 매도 후 5거래일 쿨다운, 쿨다운 뒤 재진입 허용이다.
- 기존 `vol_trailing_stop`은 스탑로스를 전달하지 않아 동작과 호출 호환성을 유지한다.
- `scores`·`items`의 기존 위치 인자는 보존하고 `stop_loss_pct`는 스레드 `kwargs`로 전달한다.
- 일일 리포트의 복합전략, `TradeRecord`/CSV 스키마, DuckDB 스키마, 스크리닝 점수 계산은 변경하지 않는다.
- 장중 저가·갭 체결 모델과 트레일링 비율·쿨다운·재진입 UI 설정은 추가하지 않는다.
- 외부 의존성을 추가하지 않고 추적되지 않은 `get-pip.py`는 수정하거나 커밋하지 않는다.

## File Map

- `backtester.py`: 공유 변동성 가중 실행 루프에서 선택적 평균 체결가 스탑로스를 판정한다.
- `tests/test_backtester.py`: 고정 시계열로 기본·커스텀 스탑로스, 트레일링, 기존 호환성, 쿨다운을 검증한다.
- `app.py`: 새 전략 허용, 입력 검증, 작업 전달·디스패치·결과 설정, UI 입력과 요청 직렬화를 담당한다.
- `tests/test_app.py`: API 경계, 작업 디스패치, 결과 설정, 템플릿 계약을 검증한다.
- `README.md`: 여섯 전략과 트레일링 스탑·평균 체결가 스탑로스의 차이를 설명한다.

---

### Task 1: 공유 엔진에 평균 체결가 스탑로스 추가

**Files:**
- Modify: `backtester.py:494-611`
- Test: `tests/test_backtester.py:17-64`

**Interfaces:**
- Consumes: `Portfolio.positions[ticker]['avg_price']: float`, `Portfolio.sell(ticker, price, shares, date)`
- Produces: `BacktestEngine.run_volatility_trailing_stop(..., stop_loss_pct: Optional[float] = None)`

- [ ] **Step 1: 스탑로스와 기존 동작을 고정하는 실패 테스트 작성**

`tests/test_backtester.py`에 다음 테스트 클래스를 `BacktestAccountingTest` 뒤에 추가한다.

```python
class VolatilityTrailingStopLossTest(unittest.TestCase):
    @staticmethod
    def run_strategy(closes, stop_loss_pct=None, slippage_pct=0.0):
        engine = BacktestEngine(
            initial_capital=1_000,
            slippage_pct=slippage_pct,
        )
        engine.add_price_data(
            "AAA",
            [
                price(f"2026-01-{index + 2:02d}", close)
                for index, close in enumerate(closes)
            ],
            name="테스트",
        )
        engine.run_volatility_trailing_stop(
            ["AAA"],
            lookback=20,
            stop_pct=-10.0,
            cooldown=5,
            reentry=True,
            stop_loss_pct=stop_loss_pct,
        )
        return engine

    def test_seven_percent_stop_loss_uses_average_execution_price(self):
        engine = self.run_strategy(
            [100, 94.5, 93.9],
            stop_loss_pct=7.0,
            slippage_pct=1.0,
        )

        closed = [trade for trade in engine.portfolio.trades if trade.status == "closed"]
        self.assertEqual([trade.exit_date for trade in closed], ["2026-01-04"])
        self.assertNotIn("AAA", engine.portfolio.positions)

    def test_custom_stop_loss_changes_exit_date(self):
        cases = ((5.0, [100, 94, 92], "2026-01-03"), (8.0, [100, 94, 92], "2026-01-04"))
        for stop_loss_pct, closes, expected_date in cases:
            with self.subTest(stop_loss_pct=stop_loss_pct):
                engine = self.run_strategy(closes, stop_loss_pct=stop_loss_pct)
                closed = [
                    trade for trade in engine.portfolio.trades
                    if trade.status == "closed"
                ]
                self.assertEqual([trade.exit_date for trade in closed], [expected_date])

    def test_trailing_stop_still_sells_a_profitable_position(self):
        engine = self.run_strategy([100, 120, 108], stop_loss_pct=7.0)

        closed = [trade for trade in engine.portfolio.trades if trade.status == "closed"]
        self.assertEqual([trade.exit_date for trade in closed], ["2026-01-04"])
        self.assertGreater(closed[0].pnl, 0)

    def test_none_stop_loss_preserves_legacy_trailing_only_behavior(self):
        engine = self.run_strategy([100, 93], stop_loss_pct=None)

        self.assertIn("AAA", engine.portfolio.positions)
        self.assertTrue(all(trade.status == "open" for trade in engine.portfolio.trades))

    def test_stop_loss_reentry_waits_for_five_complete_trading_days(self):
        engine = self.run_strategy(
            [100, 93, 93, 93, 93, 93, 93, 93],
            stop_loss_pct=7.0,
        )

        self.assertEqual(len(engine.portfolio.trades), 2)
        self.assertEqual(engine.portfolio.trades[0].exit_date, "2026-01-03")
        self.assertEqual(engine.portfolio.trades[1].entry_date, "2026-01-09")
        self.assertEqual(engine.portfolio.trades[1].status, "open")
```

- [ ] **Step 2: 새 엔진 계약이 아직 없어서 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_backtester.VolatilityTrailingStopLossTest -v`

Expected: 5개 테스트가 `unexpected keyword argument 'stop_loss_pct'`로 실패한다.

- [ ] **Step 3: 기존 실행 루프에 선택적 스탑로스 최소 구현**

`backtester.py`의 시그니처와 문서 인자를 다음과 같이 확장한다.

```python
    def run_volatility_trailing_stop(self, tickers: List[str],
                                      start_date: str = None,
                                      end_date: str = None,
                                      lookback: int = 20,
                                      stop_pct: float = -10.0,
                                      cooldown: int = 5,
                                      reentry: bool = True,
                                      stop_loss_pct: Optional[float] = None):
```

```python
            stop_loss_pct: 실제 평균 체결가 기준 고정 손절 비율
                           (양수 %, None이면 비활성화)
```

기존 피크 갱신 뒤의 스탑 체크 블록을 다음 코드로 교체한다.

```python
                pk = peaks.get(t, p)
                trailing_stop_hit = False
                if pk > 0:
                    dd_pct = (p / pk - 1) * 100
                    trailing_stop_hit = dd_pct <= stop_pct

                position = self.portfolio.positions.get(t)
                entry_stop_hit = False
                if position is not None and stop_loss_pct is not None:
                    entry_return_pct = (p / position['avg_price'] - 1) * 100
                    entry_stop_hit = entry_return_pct <= -stop_loss_pct

                if trailing_stop_hit or entry_stop_hit:
                    if position is not None:
                        self.portfolio.sell(t, p, position['shares'], date)
                    holding[t] = False
                    sold_day[t] = i
```

- [ ] **Step 4: 엔진 테스트와 기존 회계를 함께 검증**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_backtester -v`

Expected: 7개 테스트가 모두 통과한다.

- [ ] **Step 5: 엔진 변경 커밋**

```bash
git add backtester.py tests/test_backtester.py
git commit -m "Protect volatility entries without splitting the strategy loop" -m "Constraint: Preserve legacy trailing-stop behavior when stop_loss_pct is omitted
Rejected: Duplicate a second volatility strategy loop | It would let weighting and reentry behavior drift
Confidence: high
Scope-risk: narrow
Directive: Keep stop-loss evaluation based on the portfolio average execution price
Tested: unittest tests.test_backtester
Not-tested: Live Flask backtest execution"
```

---

### Task 2: API 입력 검증과 작업 디스패치 연결

**Files:**
- Modify: `app.py:285-432`
- Modify: `app.py:460-527`
- Test: `tests/test_app.py:139-289`

**Interfaces:**
- Consumes: `BacktestEngine.run_volatility_trailing_stop(..., stop_loss_pct: Optional[float])`
- Produces: `run_backtest_task(..., stop_loss_pct: float = 7.0)`, 요청 필드 `stop_loss`, 결과 필드 `config.stop_loss_pct`

- [ ] **Step 1: API 기본값·커스텀값·오류·디스패치 실패 테스트 작성**

`test_backtest_api_passes_default_filters_to_worker`에 다음 검증을 추가한다.

```python
        self.assertEqual(thread.call_args.kwargs["kwargs"], {"stop_loss_pct": 7.0})
```

`FlaskApiTest`에 다음 두 메서드를 추가한다.

```python
    def test_backtest_api_accepts_new_strategy_and_custom_stop_loss(self):
        with patch.object(app_module.threading, "Thread") as thread:
            response = self.client.post(
                "/api/backtest/run",
                json={
                    "strategy": "vol_trailing_stop_loss",
                    "stop_loss": "12.5",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(thread.call_args.kwargs["kwargs"], {"stop_loss_pct": 12.5})
        thread.return_value.start.assert_called_once_with()

    def test_backtest_api_rejects_invalid_stop_loss_before_starting_worker(self):
        invalid_values = ("not-a-number", "nan", "inf", 0, 0.09, 50.01)
        for value in invalid_values:
            with self.subTest(value=value):
                with patch.object(app_module.threading, "Thread") as thread:
                    response = self.client.post(
                        "/api/backtest/run",
                        json={"stop_loss": value},
                    )
                self.assertEqual(response.status_code, 400)
                self.assertIn("스탑로스", response.get_json()["error"])
                thread.assert_not_called()
```

`test_backtest_result_records_effective_filters`의 작업 실행을 다음 코드로 교체한다.

```python
            app_module.run_backtest_task(
                6,
                100_000_000,
                "vol_trailing_stop_loss",
                score_filters=(),
                item_filters=("turnaround",),
                stop_loss_pct=12.5,
            )
```

그 뒤 다음 검증을 추가한다.

```python
        engine.run_volatility_trailing_stop.assert_called_once_with(
            ["000001"],
            lookback=20,
            stop_pct=-10.0,
            cooldown=5,
            reentry=True,
            stop_loss_pct=12.5,
        )
        self.assertEqual(config["strategy"], "vol_trailing_stop_loss")
        self.assertEqual(
            config["strategy_name"],
            "변동성 가중 + 트레일링 스탑 + 스탑로스",
        )
        self.assertEqual(config["stop_loss_pct"], 12.5)
```

- [ ] **Step 2: API·작업 테스트가 새 계약 부재로 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.FlaskApiTest.test_backtest_api_passes_default_filters_to_worker tests.test_app.FlaskApiTest.test_backtest_api_accepts_new_strategy_and_custom_stop_loss tests.test_app.FlaskApiTest.test_backtest_api_rejects_invalid_stop_loss_before_starting_worker tests.test_app.FlaskApiTest.test_backtest_result_records_effective_filters -v`

Expected: 스레드 `kwargs`, 새 전략 허용, 입력 검증, 결과 설정 검증이 실패한다.

- [ ] **Step 3: 작업 함수·디스패치·결과 설정 구현**

`run_backtest_task` 시그니처를 확장한다.

```python
def run_backtest_task(period_months, initial_capital, strategy,
                      slippage_pct=0.3, commission_pct=0.015, tax_pct=0.20,
                      score_filters=DEFAULT_BACKTEST_SCORES, item_filters=(),
                      stop_loss_pct=7.0):
```

기존 `vol_trailing_stop` 분기 바로 뒤에 새 분기를 추가한다.

```python
        elif strategy == 'vol_trailing_stop_loss':
            engine.run_volatility_trailing_stop(
                tickers, lookback=20, stop_pct=-10.0,
                cooldown=5, reentry=True,
                stop_loss_pct=stop_loss_pct)
```

전략 표시명 맵과 결과 설정에 다음 값을 추가한다.

```python
            'vol_trailing_stop_loss': '변동성 가중 + 트레일링 스탑 + 스탑로스',
```

```python
            'stop_loss_pct': stop_loss_pct,
```

- [ ] **Step 4: API 경계 검증과 키워드 작업 전달 구현**

기존 숫자 변환 뒤에 다음 검증을 추가한다.

```python
    try:
        stop_loss = float(params.get('stop_loss', 7.0))
    except (TypeError, ValueError):
        return jsonify({'error': '스탑로스는 0.1~50 사이의 숫자여야 합니다.'}), 400
    if not math.isfinite(stop_loss) or not 0.1 <= stop_loss <= 50.0:
        return jsonify({'error': '스탑로스는 0.1~50 사이의 숫자여야 합니다.'}), 400
```

허용 전략 집합에 다음 키를 추가한다.

```python
        'vol_trailing_stop_loss',
```

스레드 생성에 키워드 인자를 추가한다.

```python
        kwargs={'stop_loss_pct': stop_loss},
```

- [ ] **Step 5: API 및 전체 Flask 테스트 통과 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app -v`

Expected: `tests.test_app` 전체가 통과하고 기존 필터 위치 인자 검증도 유지된다.

- [ ] **Step 6: API 변경 커밋**

```bash
git add app.py tests/test_app.py
git commit -m "Expose entry-loss protection as a reproducible backtest strategy" -m "Constraint: Accept only finite stop-loss values from 0.1 through 50.0 percent
Rejected: Add stop loss as another positional worker argument | Existing filter callers rely on the current argument order
Confidence: high
Scope-risk: moderate
Directive: Keep vol_trailing_stop_loss dispatch on the shared engine method
Tested: unittest tests.test_app
Not-tested: Browser rendering and live market-data run"
```

---

### Task 3: 웹 입력과 README 계약 공개

**Files:**
- Modify: `app.py:1117-1153`
- Modify: `app.py:1263-1284`
- Modify: `README.md:7-13`
- Modify: `README.md:138-177`
- Modify: `README.md:217-229`
- Test: `tests/test_app.py:261-289`

**Interfaces:**
- Consumes: 요청 필드 `stop_loss: float`, 전략 키 `vol_trailing_stop_loss`
- Produces: DOM 입력 `#cfgStopLoss`, 기본값 `7`, 범위 `0.1..50`, 간격 `0.5`

- [ ] **Step 1: 템플릿 계약 실패 테스트 작성**

`FlaskApiTest`에 다음 메서드를 추가한다.

```python
    def test_backtest_page_exposes_and_sends_custom_stop_loss(self):
        template = app_module.BACKTEST_TEMPLATE

        self.assertIn(
            '<option value="vol_trailing_stop_loss">🛡️ 변동성 가중 + 트레일링 스탑 + 스탑로스</option>',
            template,
        )
        self.assertIn('id="cfgStopLoss"', template)
        self.assertIn('value="7"', template)
        self.assertIn('min="0.1"', template)
        self.assertIn('max="50"', template)
        self.assertIn('step="0.5"', template)
        self.assertIn("새 스탑로스 전략에만 적용", template)
        self.assertIn(
            "stop_loss: +document.getElementById('cfgStopLoss').value",
            template,
        )
```

- [ ] **Step 2: 템플릿 테스트가 새 요소 부재로 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app.FlaskApiTest.test_backtest_page_exposes_and_sends_custom_stop_loss -v`

Expected: 새 전략 옵션 또는 `cfgStopLoss`가 없어서 실패한다.

- [ ] **Step 3: 전략 옵션·숫자 입력·요청 본문 구현**

`cfgStrategy`의 기존 트레일링 옵션 바로 뒤에 다음 옵션을 추가한다.

```html
                <option value="vol_trailing_stop_loss">🛡️ 변동성 가중 + 트레일링 스탑 + 스탑로스</option>
```

전략 설정 그룹 뒤에 다음 입력 그룹을 추가한다.

```html
        <div class="cfg-group">
            <label>스탑로스 (%) <span>새 스탑로스 전략에만 적용</span></label>
            <input type="number" id="cfgStopLoss" value="7" min="0.1" max="50" step="0.5" style="width:90px">
        </div>
```

`runBacktest()` 요청 객체의 `strategy` 다음에 다음 필드를 추가한다.

```javascript
        stop_loss: +document.getElementById('cfgStopLoss').value,
```

- [ ] **Step 4: README에 여섯 번째 전략과 위험 기준 문서화**

`README.md`의 로컬 웹 기능을 `6개 백테스트 전략`으로 바꾸고 전략 표에 다음 행을 추가한다.

```markdown
| `vol_trailing_stop_loss` | 변동성 가중 + 트레일링 스탑 + 스탑로스 | 저변동성 비중 확대, 최고가 대비 10% 하락 또는 평균 체결가 대비 설정 손실률 도달 시 매도 |
```

전략 표 아래 설정 설명을 다음 문단으로 확장한다.

```markdown
`vol_trailing_stop_loss`의 스탑로스 기본값은 7%이며 웹과 API에서 0.1%~50% 범위로 변경할 수 있습니다. 기존 트레일링 스탑은 보유 중 최고 종가 대비 10% 하락을 추적하고, 새 스탑로스는 매수 슬리피지를 포함한 실제 평균 체결가 대비 손실을 제한합니다. 둘 중 하나가 충족되면 전량 매도하며 5거래일 쿨다운 뒤 재진입할 수 있습니다.
```

GitHub Actions 실행 순서 뒤에 다음 문장을 추가한다.

```markdown
GitHub Actions 일일 리포트는 웹의 새 스탑로스 전략과 무관하게 기존 6개월 복합전략을 계속 사용합니다.
```

- [ ] **Step 5: 템플릿·문서 및 회귀 테스트 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app -v`

Expected: Flask 테스트 전체가 통과한다.

Run: `rg -n "6개 백테스트|vol_trailing_stop_loss|기본값은 7%|기존 6개월 복합전략" README.md app.py`

Expected: README와 템플릿/API에 새 전략 계약이 모두 검색된다.

- [ ] **Step 6: UI와 문서 변경 커밋**

```bash
git add app.py tests/test_app.py README.md
git commit -m "Let backtest users tune entry-loss protection explicitly" -m "Constraint: Keep the control visible but document that it applies only to the new strategy
Rejected: Hide and reveal the control on strategy changes | Permanent visibility keeps the static template simple and testable
Confidence: high
Scope-risk: narrow
Directive: Document the distinction between peak-based trailing and entry-based stop loss
Tested: unittest tests.test_app; README contract search
Not-tested: Current screened-stock market-data execution"
```

---

### Task 4: 전체 회귀 및 국민연금 단독 1점 종단간 검증

**Files:**
- Verify: `app.py`
- Verify: `backtester.py`
- Verify: `README.md`
- Verify: `tests/test_app.py`
- Verify: `tests/test_backtester.py`

**Interfaces:**
- Consumes: `GET /api/status`, `POST /api/backtest/run`, `GET /api/backtest/status`
- Produces: 현재 `종합점수 == 1`이면서 `출처 == "국민연금 신규/추가매수"`인 전체 후보에 대한 완료 결과

- [ ] **Step 1: 정적·전체 회귀 검증 실행**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -v`

Expected: 전체 테스트가 0 failures, 0 errors로 통과한다.

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m py_compile app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py`

Expected: 종료 코드 0이며 출력이 없다.

Run: `uvx ruff check app.py backtester.py daily_report.py nps_tracker.py screening.py stock_db.py stock_screener.py tests`

Expected: `All checks passed!`

Run: `git diff --check && git status --short`

Expected: 공백 오류가 없고 `get-pip.py` 외 미커밋 파일이 없다.

- [ ] **Step 2: 기능 브랜치를 로컬 master에 fast-forward하고 앱 재시작**

기존 앱 프로세스의 PID와 명령을 `lsof -nP -iTCP:5000 -sTCP:LISTEN`과 `ps -p <PID> -o pid=,command=`로 확인하고 `kill -TERM <PID>`로 정상 종료한다. 저장소 루트에서 `git merge --ff-only feature/volatility-stop-loss`로 `master`를 기능 브랜치까지 fast-forward한다. 다음 한 줄 명령으로 새 코드를 실행하고 `GET /backtest`가 HTTP 200이 될 때까지 확인한다.

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python app.py`

Expected: `http://127.0.0.1:5000`에서 Flask 앱이 실행되고 `/backtest` 응답에 `vol_trailing_stop_loss`와 `cfgStopLoss`가 포함된다.

- [ ] **Step 3: 실행 직전 정확 후보 수 계산**

`GET /api/status`의 `result`에서 다음 조건을 모두 만족하는 행만 센다.

```python
row.get("종합점수") == 1 and row.get("출처") == "국민연금 신규/추가매수"
```

Expected: 후보 수가 1개 이상이며 검증 로그에 실행 시각과 후보 수를 기록한다. 설계 시점의 84개를 하드코딩하지 않는다.

- [ ] **Step 4: 새 전략으로 실제 백테스트 시작**

`POST /api/backtest/run`에 다음 JSON을 보낸다.

```json
{
  "period": 6,
  "capital": 100000000,
  "strategy": "vol_trailing_stop_loss",
  "stop_loss": 7,
  "scores": [1],
  "items": ["nps"],
  "slippage": 0.3,
  "commission": 0.015,
  "tax": 0.2
}
```

Expected: HTTP 200과 `status == "started"`를 반환한다.

- [ ] **Step 5: 완료까지 폴링하고 결과 계약 검증**

`GET /api/backtest/status`를 5초 간격으로 폴링하되 사용자에게 60초 이내 간격으로 진행 상태를 보고한다. `status == "error"`이면 `error_msg`와 앱 로그를 진단하고 수정·재검증한다. `status == "done"`이면 다음 조건을 모두 단언한다.

```python
results["config"]["strategy"] == "vol_trailing_stop_loss"
results["config"]["stop_loss_pct"] == 7.0
results["config"]["score_filters"] == [1]
results["config"]["item_filters"] == ["nps"]
results["config"]["total_stocks"] + len(results["config"]["unmatched"]) == candidate_count
results["config"]["loaded_stocks"] >= 1
isinstance(results["metrics"], dict)
isinstance(results["cost_summary"], dict)
isinstance(results["stock_performance"], list)
isinstance(results["trades"], list)
```

후보 원본에도 다음 조건을 다시 적용해 요청에 투입된 집합이 국민연금 단독 1점 종목만 포함하는지 확인한다.

```python
all(
    row.get("종합점수") == 1
    and row.get("출처") == "국민연금 신규/추가매수"
    for row in candidates
)
```

- [ ] **Step 6: 잘못된 스탑로스와 화면 응답 스모크 검증**

Run: `curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/backtest`

Expected: `200`

`stop_loss: 0`과 `strategy: vol_trailing_stop_loss`를 보낸다.

Expected: HTTP 400이며 응답 오류에 `스탑로스`가 포함된다.

- [ ] **Step 7: 최종 변경 상태·커밋·원격 푸시 검증**

Run: `git status --short --branch && git log --oneline --decorate origin/master..HEAD`

Expected: `get-pip.py` 외 미커밋 파일이 없고 설계·계획·구현 커밋이 `origin/master` 앞에 있다.

실제 종단간 실행에서 코드 수정이 필요했다면 관련 테스트와 함께 Lore 형식의 보정 커밋을 만든 뒤 Step 1부터 재검증한다. 모든 검증이 통과하면 기능 작업공간을 정리하고 다음 명령으로 승인된 `master`를 푸시한다.

Run: `git push origin master`

Expected: 원격 `master`가 로컬 `HEAD`로 fast-forward되고 `git status --short --branch`가 원격과 동기화된 상태에서 `?? get-pip.py`만 표시한다.
