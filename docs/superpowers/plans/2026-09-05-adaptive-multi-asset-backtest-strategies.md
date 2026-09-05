# Adaptive Multi-Asset Backtest Strategies Implementation Plan

> **상태 (2026-09-05):** 구현 및 검증 완료. 계획 초안의 단계별 체크박스는 당시 실행 순서를 보존하며, 최종 근거는 `reports/adaptive_evaluation.md`와 후속 커밋 기록에 반영한다. 표본외 구간은 위험예산 선택에 사용하지 않고 고정된 값으로 평가한다.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 국내 상장 ETF 기반 적응형 자산배분 전략 4개와 공통 10% 낙폭 제어기를 기존 백테스트에 추가하고, 비용 포함 표본외 검증을 통해 최소 2개 전략의 위험·성과 기준 통과를 확인한다.

**Architecture:** `strategy_catalog.py`가 기존·신규 전략과 고정 ETF 유니버스의 단일 진실 공급원이 된다. `adaptive_strategies.py`는 가격 이력에서 순수 목표비중을 계산하고, `drawdown_guard.py`는 그 목표비중에 공통 위험예산을 적용하며, `BacktestEngine`은 전일 신호를 다음 거래일 시가에 실행한다. `stock_db.py`는 조정가격 출처를 구분해 저장하고 Flask·일일 리포트·CSV는 중앙 카탈로그와 공통 결과 계약을 사용한다.

**Tech Stack:** Python 3.11, Flask, DuckDB, yfinance, 표준 `unittest`, `unittest.mock`, vanilla HTML/CSS/JavaScript, `uv`, Ruff

**Spec:** `docs/superpowers/specs/2026-09-05-adaptive-multi-asset-backtest-strategies-design.md`

## Global Constraints

- 기존 6개 전략 키와 API 결과 계약을 유지한다.
- 신규 전략 키는 `defensive_dual_momentum`, `multi_asset_trend_rotation`, `trend_risk_parity`, `price_regime_ensemble`이다.
- 신규 전략은 고정 국내 상장 ETF만 사용하며 현재 스크리닝 결과를 과거에 소급해 생존편향을 만들지 않는다.
- 신호는 거래일 `t-1` 종가까지의 데이터로 계산하고 거래일 `t` 시가에 실행한다.
- 공통 낙폭 제어기는 8%에서 위험자산을 50%로 감속하고 10%에서 100% 현금화한다.
- 차단 후 최소 20거래일 대기하고 위험예산을 25%씩 단계 재진입한다.
- 키움증권 온라인 ETF 거래수수료 기본값은 편도 `0.015%`다.
- ETF 슬리피지 기본값은 편도 `0.10%`다.
- ETF 증권거래세는 `0%`이며 보유기간과세를 제외한 세전 성과로 표시한다.
- ETF 가격은 `yfinance auto_adjust=True` 조정가격만 사용하며 pykrx 원가격과 섞지 않는다.
- 레버리지, 인버스, 공매도, 뉴스 감성, HMM과 새 외부 의존성은 추가하지 않는다.
- 사용자 요청 전에는 `git commit`을 만들지 않는다. 각 작업은 검증 결과와 `git diff` 검토로 종료한다.

## File Map

- Create `strategy_catalog.py`: 전략 메타데이터, ETF 자산 메타데이터, 전략 그룹 조회를 담당한다.
- Create `strategy_runner.py`: 기존·신규 전략 실행 분기를 한 곳에서 담당한다.
- Create `adaptive_strategies.py`: 모멘텀·추세·변동성과 네 전략의 순수 목표비중을 계산한다.
- Create `drawdown_guard.py`: 8% 감속, 10% 차단, 20일 대기와 단계 재진입 상태를 관리한다.
- Modify `stock_db.py`: 조정가격 출처를 저장하고 고정 ETF를 yfinance 전용으로 수집·조회한다.
- Modify `backtester.py`: 목표비중 주문 실행, 신규 전략 일별 루프, 레짐·비중·제어 이력을 결과에 포함한다.
- Modify `app.py`: 중앙 카탈로그 기반 API 검증·디스패치·화면·CSV를 제공한다.
- Modify `daily_report.py`: 전략명과 디스패치를 중앙 카탈로그로 통합하고 기존 분기 누락을 제거한다.
- Create `scripts/evaluate_adaptive_strategies.py`: 공통기간·표본외·롤링·충격구간 성과를 재현 가능하게 평가한다.
- Create `tests/test_strategy_catalog.py`: 카탈로그 유일성, 자산 계약, 전략 그룹을 검증한다.
- Create `tests/test_strategy_runner.py`: 모든 전략 키의 엔진 호출 계약을 검증한다.
- Create `tests/test_adaptive_strategies.py`: 신호와 네 목표비중 정책을 결정적 시계열로 검증한다.
- Create `tests/test_drawdown_guard.py`: 낙폭 제어 상태 전이를 검증한다.
- Modify `tests/test_stock_db.py`: 조정가격 스키마·수집·혼합 방지를 검증한다.
- Modify `tests/test_backtester.py`: 다음날 체결, 목표비중 실행, 결과 이력을 검증한다.
- Modify `tests/test_app.py`: API·UI·CSV 계약과 기존 전략 호환성을 검증한다.
- Modify `tests/test_daily_report.py`: 카탈로그 기반 전략명·디스패치를 검증한다.
- Modify `README.md`: 신규 전략, ETF 유니버스, 비용·세금·MDD 한계를 문서화한다.

---

### Task 1: 전략과 ETF 유니버스 중앙 카탈로그

**Files:**
- Create: `strategy_catalog.py`
- Create: `strategy_runner.py`
- Create: `tests/test_strategy_catalog.py`
- Create: `tests/test_strategy_runner.py`

**Interfaces:**
- Produces: `AssetSpec`, `StrategySpec`, `ETF_ASSETS`, `STRATEGIES`, `ETF_STRATEGY_KEYS`
- Produces: `get_strategy(key: str) -> StrategySpec`, `is_etf_strategy(key: str) -> bool`, `strategy_groups() -> tuple[tuple[str, tuple[StrategySpec, ...]], ...]`
- Produces: `run_strategy(engine, key: str, tickers: list[str], start_date: Optional[str] = None, end_date: Optional[str] = None, stop_loss_pct: float = 7.0) -> None`
- Consumed by: `adaptive_strategies.py`, `backtester.py`, `app.py`, `daily_report.py`

- [ ] **Step 1: 카탈로그 계약 실패 테스트 작성**

`tests/test_strategy_catalog.py`에 정확히 다음 축을 검증한다.

```python
import unittest

from strategy_catalog import (
    ETF_ASSETS,
    ETF_STRATEGY_KEYS,
    STRATEGIES,
    get_strategy,
    is_etf_strategy,
    strategy_groups,
)


class StrategyCatalogTest(unittest.TestCase):
    def test_catalog_has_six_legacy_and_four_etf_strategies(self):
        self.assertEqual(len(STRATEGIES), 10)
        self.assertEqual(
            ETF_STRATEGY_KEYS,
            frozenset({
                "defensive_dual_momentum",
                "multi_asset_trend_rotation",
                "trend_risk_parity",
                "price_regime_ensemble",
            }),
        )
        self.assertTrue(is_etf_strategy("trend_risk_parity"))
        self.assertFalse(is_etf_strategy("composite"))

    def test_etf_universe_has_unique_tickers_and_required_roles(self):
        self.assertEqual(len(ETF_ASSETS), 8)
        self.assertEqual(len({asset.ticker for asset in ETF_ASSETS.values()}), 8)
        self.assertEqual(
            {asset.role for asset in ETF_ASSETS.values()},
            {"risk", "defensive", "real_asset"},
        )
        self.assertEqual(ETF_ASSETS["kr_equity"].ticker, "069500")
        self.assertEqual(ETF_ASSETS["us_equity"].ticker, "143850")
        self.assertEqual(ETF_ASSETS["oil"].ticker, "130680")

    def test_groups_return_stable_catalog_order(self):
        groups = strategy_groups()
        self.assertEqual([name for name, _ in groups], ["기존 전략", "레짐·자산배분 전략"])
        flattened = [spec.key for _, specs in groups for spec in specs]
        self.assertEqual(flattened, list(STRATEGIES))
        self.assertEqual(get_strategy("defensive_dual_momentum").kind, "etf")
```

- [ ] **Step 2: 테스트가 모듈 부재로 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_strategy_catalog -v`

Expected: `ModuleNotFoundError: No module named 'strategy_catalog'`

- [ ] **Step 3: 고정 데이터 클래스와 카탈로그 구현**

`strategy_catalog.py`는 다음 공개 계약을 제공한다.

```python
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Tuple


@dataclass(frozen=True)
class AssetSpec:
    key: str
    ticker: str
    name: str
    asset_class: str
    role: str
    max_weight: float


@dataclass(frozen=True)
class StrategySpec:
    key: str
    label: str
    description: str
    kind: str
    rebalance: str


ETF_ASSETS: Mapping[str, AssetSpec] = MappingProxyType({
    "kr_equity": AssetSpec("kr_equity", "069500", "KODEX 200", "equity", "risk", 0.40),
    "us_equity": AssetSpec("us_equity", "143850", "TIGER 미국S&P500선물(H)", "equity", "risk", 0.40),
    "us_tech": AssetSpec("us_tech", "133690", "TIGER 미국나스닥100", "equity", "risk", 0.30),
    "kr_bond_10y": AssetSpec("kr_bond_10y", "148070", "KIWOOM 국고채10년", "bond", "defensive", 0.40),
    "short_bond": AssetSpec("short_bond", "153130", "KODEX 단기채권", "cash_like", "defensive", 0.50),
    "gold": AssetSpec("gold", "132030", "KODEX 골드선물(H)", "commodity", "defensive", 0.30),
    "usd": AssetSpec("usd", "261240", "KODEX 미국달러선물", "currency", "defensive", 0.25),
    "oil": AssetSpec("oil", "130680", "TIGER 원유선물Enhanced(H)", "commodity", "real_asset", 0.10),
})
```

`STRATEGIES`에는 기존 6개를 현재 화면 순서로 먼저 넣고 신규 4개를 설계 순서로 넣는다. `get_strategy()`는 없는 키에 `KeyError`를 발생시키고, `strategy_groups()`는 삽입 순서를 유지한 불변 튜플을 반환한다.

`strategy_runner.py`의 `run_strategy()`는 실행 분기를 한 번만 정의한다.

```python
def run_strategy(engine, key, tickers, start_date=None, end_date=None, stop_loss_pct=7.0):
    if is_etf_strategy(key):
        engine.run_adaptive_strategy(key, start_date=start_date, end_date=end_date)
    elif key == "rebalance":
        engine.run_rebalance(tickers, start_date, end_date, period=20)
    elif key == "vol_trailing_stop":
        engine.run_volatility_trailing_stop(tickers, start_date, end_date, lookback=20, stop_pct=-10.0, cooldown=5, reentry=True)
    elif key == "vol_trailing_stop_loss":
        engine.run_volatility_trailing_stop(tickers, start_date, end_date, lookback=20, stop_pct=-10.0, cooldown=5, reentry=True, stop_loss_pct=stop_loss_pct)
    elif key == "ma_filter":
        engine.run_ma_filter(tickers, start_date, end_date, ma_period=20, rebalance_period=5)
    elif key == "composite":
        engine.run_composite(tickers, start_date, end_date, ma_period=20, lookback=20, stop_pct=-8.0, cooldown=5, rebalance_period=10)
    elif key == "equal_weight":
        engine.run_equal_weight(tickers, start_date, end_date)
    else:
        raise KeyError(key)
```

`tests/test_strategy_runner.py`는 `Mock()` 엔진으로 10개 키를 순회하고 정확한 메서드와 키워드 인자가 한 번 호출되는지 검증한다. 특히 `vol_trailing_stop_loss`의 `stop_loss_pct`와 ETF 전략의 `strategy_key` 전달을 별도 단언한다.

- [ ] **Step 4: 카탈로그 테스트와 정적 검사 통과 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_strategy_catalog tests.test_strategy_runner -v`

Run: `uvx ruff check strategy_catalog.py strategy_runner.py tests/test_strategy_catalog.py tests/test_strategy_runner.py`

Expected: 모든 테스트와 Ruff 통과.

- [ ] **Step 5: 변경 범위 검토**

Run: `git diff --check && git diff -- strategy_catalog.py strategy_runner.py tests/test_strategy_catalog.py tests/test_strategy_runner.py`

Stop condition: 카탈로그가 10개 전략과 8개 ETF를 유일한 순서로 제공하고 다른 파일은 수정되지 않았다.

---

### Task 2: ETF 조정가격 저장·조회 계약

**Files:**
- Modify: `stock_db.py`
- Modify: `tests/test_stock_db.py`

**Interfaces:**
- Consumes: `AssetSpec.ticker`, `AssetSpec.name`
- Produces: `StockDB.ensure_adjusted_etf_data(tickers: list[str], start_yyyymmdd: str, end_yyyymmdd: str, names: dict[str, str], progress_callback=None) -> dict`
- Produces: `StockDB.get_adjusted_prices_many(tickers: list[str], start_date: str, end_date: str) -> dict[str, list[dict]]`
- Schema: `daily_prices.price_source VARCHAR`, `daily_prices.is_adjusted BOOLEAN`

- [ ] **Step 1: 스키마 마이그레이션과 혼합 방지 실패 테스트 작성**

`tests/test_stock_db.py`에 다음 사례를 추가한다.

```python
    def test_daily_prices_tracks_source_and_adjustment_contract(self):
        columns = {
            row[1]: row[2]
            for row in self.db._connect().execute(
                "PRAGMA table_info('daily_prices')"
            ).fetchall()
        }
        self.assertEqual(columns["price_source"], "VARCHAR")
        self.assertEqual(columns["is_adjusted"], "BOOLEAN")

    @patch("stock_db.yf.download")
    def test_adjusted_etf_fetch_uses_ks_symbol_and_adjusted_prices(self, download):
        download.return_value = self.price_frame(close=12_345.0)

        stats = self.db.ensure_adjusted_etf_data(
            ["069500"], "20260102", "20260105", {"069500": "KODEX 200"}
        )

        self.assertEqual(stats["fetched"], 1)
        self.assertEqual(download.call_args.args[0], "069500.KS")
        self.assertTrue(download.call_args.kwargs["auto_adjust"])
        rows = self.db.get_adjusted_prices_many(
            ["069500"], "2026-01-02", "2026-01-05"
        )
        self.assertEqual(rows["069500"][0]["close"], 12_345.0)

    def test_adjusted_reader_rejects_legacy_or_raw_rows(self):
        self.db.save_prices(
            "069500",
            [{"date": "2026-01-02", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1}],
            name="KODEX 200",
        )
        with self.assertRaisesRegex(ValueError, "조정가격"):
            self.db.get_adjusted_prices_many(
                ["069500"], "2026-01-02", "2026-01-02"
            )
```

`price_frame()`은 기존 yfinance 테스트의 pandas DataFrame 헬퍼 패턴을 재사용하되 `Open/High/Low/Close/Volume` 한 행을 반환한다.

- [ ] **Step 2: 새 메서드와 스키마가 없어 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db.StockDbCacheTest.test_daily_prices_tracks_source_and_adjustment_contract tests.test_stock_db.StockDbCacheTest.test_adjusted_etf_fetch_uses_ks_symbol_and_adjusted_prices tests.test_stock_db.StockDbCacheTest.test_adjusted_reader_rejects_legacy_or_raw_rows -v`

Expected: 새 컬럼 또는 메서드 부재로 3개 테스트 실패.

- [ ] **Step 3: 비파괴 스키마 마이그레이션 구현**

`_init_tables()`에 다음 컬럼을 추가한다.

```sql
ALTER TABLE daily_prices ADD COLUMN IF NOT EXISTS price_source VARCHAR;
ALTER TABLE daily_prices ADD COLUMN IF NOT EXISTS is_adjusted BOOLEAN;
UPDATE daily_prices SET price_source = 'legacy' WHERE price_source IS NULL;
UPDATE daily_prices SET is_adjusted = FALSE WHERE is_adjusted IS NULL;
```

`save_prices()`에 키워드 전용 인자 `price_source: str = "legacy"`, `is_adjusted: bool = False`를 추가하고 INSERT/UPSERT에 두 값을 저장한다. 기존 호출은 기본값으로 이전 동작을 유지한다.

- [ ] **Step 4: yfinance 전용 ETF 수집과 엄격 조회 구현**

`ensure_adjusted_etf_data()`는 각 티커에 대해 `f"{ticker}.KS"` 하나만 `_download_yfinance()`로 조회한다. 저장 시 `price_source="yfinance_auto_adjust"`, `is_adjusted=True`를 전달한다. 캐시 충족 여부는 요청 범위의 모든 행이 같은 출처와 `is_adjusted=TRUE`일 때만 인정한다.

`get_adjusted_prices_many()`는 기존 `get_prices_many()`와 같은 반환 형식을 사용하되 다음 조건을 쿼리에 추가한다.

```sql
AND price_source = 'yfinance_auto_adjust'
AND is_adjusted = TRUE
```

요청 티커 중 한 개라도 범위 내 행이 전혀 없으면 `ValueError("ETF 조정가격이 없습니다: ...")`를 발생시킨다. 같은 범위에 legacy/raw 행만 있으면 자동 대체하지 않는다.

- [ ] **Step 5: 집중·전체 DB 회귀 검증**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_stock_db -v`

Run: `uvx ruff check stock_db.py tests/test_stock_db.py`

Expected: 기존 저장·동시성·캐시 테스트와 신규 조정가격 테스트 모두 통과.

- [ ] **Step 6: 변경 범위 검토**

Run: `git diff --check && git diff -- stock_db.py tests/test_stock_db.py`

Stop condition: 기존 가격 API는 호환되고 ETF 전용 경로는 yfinance 조정가격이 아니면 실행되지 않는다.

---

### Task 3: 순수 적응형 배분 정책

**Files:**
- Create: `adaptive_strategies.py`
- Create: `tests/test_adaptive_strategies.py`

**Interfaces:**
- Consumes: `strategy_catalog.ETF_ASSETS`, `strategy_catalog.ETF_STRATEGY_KEYS`
- Produces: `AllocationDecision(target_weights: dict[str, float], regime: str, evidence: dict[str, object])`
- Produces: `build_allocation(strategy_key: str, closes_by_ticker: dict[str, list[float]]) -> AllocationDecision`

- [ ] **Step 1: 공통 지표 실패 테스트 작성**

```python
class AdaptiveIndicatorTest(unittest.TestCase):
    def test_momentum_uses_exactly_252_prior_sessions(self):
        closes = [100.0] * 252 + [110.0]
        self.assertAlmostEqual(momentum(closes, 252), 0.10)
        self.assertIsNone(momentum(closes[:252], 252))

    def test_inverse_volatility_weights_are_normalized(self):
        weights = inverse_volatility_weights({
            "LOW": [100, 101, 102, 103, 104],
            "HIGH": [100, 110, 90, 115, 85],
        }, lookback=4)
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertGreater(weights["LOW"], weights["HIGH"])

    def test_long_trend_requires_price_above_210_day_average(self):
        self.assertTrue(has_positive_trend([100.0] * 210 + [101.0], 210))
        self.assertFalse(has_positive_trend([100.0] * 210 + [99.0], 210))
```

- [ ] **Step 2: 네 전략의 결정적 실패 테스트 작성**

합성 이력은 각 티커에 253개 종가를 제공한다. 마지막 종가만 조정해 모멘텀 순위와 레짐 투표를 제어한다.

```python
class AdaptiveAllocationTest(unittest.TestCase):
    def test_dual_momentum_selects_best_positive_risk_asset(self):
        histories = histories_with_returns({"069500": 0.05, "143850": 0.12, "133690": 0.08})
        decision = build_allocation("defensive_dual_momentum", histories)
        self.assertEqual(decision.target_weights, {"143850": 0.40, "153130": 0.50})

    def test_dual_momentum_falls_back_to_short_bond_when_all_negative(self):
        histories = histories_with_returns({"069500": -0.05, "143850": -0.03, "133690": -0.08})
        decision = build_allocation("defensive_dual_momentum", histories)
        self.assertEqual(decision.target_weights, {"153130": 0.50})

    def test_trend_rotation_selects_three_assets_and_caps_oil(self):
        decision = build_allocation("multi_asset_trend_rotation", broad_positive_histories())
        self.assertLessEqual(decision.target_weights.get("130680", 0.0), 0.10)
        self.assertLessEqual(len([weight for weight in decision.target_weights.values() if weight > 0]), 3)
        self.assertLessEqual(sum(decision.target_weights.values()), 1.0)

    def test_regime_ensemble_has_fixed_risk_off_allocation(self):
        decision = build_allocation("price_regime_ensemble", risk_off_histories())
        self.assertEqual(decision.regime, "risk-off")
        self.assertEqual(decision.target_weights, {
            "148070": 0.25,
            "153130": 0.25,
            "132030": 0.20,
            "261240": 0.15,
        })
```

- [ ] **Step 3: 모듈 부재로 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_adaptive_strategies -v`

Expected: `ModuleNotFoundError: No module named 'adaptive_strategies'`

- [ ] **Step 4: 지표와 공통 검증 구현**

```python
@dataclass(frozen=True)
class AllocationDecision:
    target_weights: dict[str, float]
    regime: str
    evidence: dict[str, object]


def momentum(closes: Sequence[float], lookback: int = 252) -> Optional[float]:
    if len(closes) < lookback + 1 or closes[-lookback - 1] <= 0:
        return None
    return closes[-1] / closes[-lookback - 1] - 1


def has_positive_trend(closes: Sequence[float], period: int = 210) -> bool:
    return len(closes) >= period + 1 and closes[-1] > statistics.fmean(closes[-period:])
```

`annualized_volatility()`는 최근 `lookback + 1` 종가의 일수익률 표준편차에 `sqrt(252)`를 곱한다. `inverse_volatility_weights()`는 변동성이 없거나 계산 불가한 자산을 제외하며 합계 1로 정규화한다. 최종 목표비중은 음수 금지, 합계 1 이하, 각 `AssetSpec.max_weight` 준수를 검증한다.

- [ ] **Step 5: 네 전략의 정확한 정책 구현**

- 듀얼 모멘텀: `069500`, `143850`, `133690` 중 252일 모멘텀 최대 자산을 선택한다. 값이 양수면 선택 위험자산 40%·`153130` 50%·현금 10%, 0 이하면 `153130` 50%·현금 50%로 둔다.
- 추세 회전: 252일 모멘텀 양수이면서 210일 MA 위인 자산을 모멘텀 내림차순 상위 3개로 제한하고 60일 역변동성 배분 후 자산별 상한을 적용하며 잔여는 현금.
- 추세 리스크패리티: 장기추세 양수 자산 전체를 60일 역변동성 배분하고 주식 합계 60%, 채권·현금성 합계 60%, 금·달러·원유 합계 40%, 원유 10% 상한을 순서대로 적용.
- 레짐 앙상블: 세 주식 자산 중 252일 모멘텀 양수이면서 210일 MA 위인 개수를 `breadth`로 계산한다. `breadth == 3`이면 `risk-on`, `breadth <= 1`이고 금·달러·국고채 중 양의 모멘텀이 2개 이상이면 `risk-off`, 그 외는 `neutral`이다.

레짐별 고정 비중은 다음과 같다.

```python
REGIME_WEIGHTS = {
    "risk-on": {"069500": 0.20, "143850": 0.35, "133690": 0.20, "148070": 0.10, "132030": 0.05, "130680": 0.05},
    "neutral": {"069500": 0.10, "143850": 0.20, "133690": 0.10, "148070": 0.25, "153130": 0.15, "132030": 0.10, "261240": 0.05},
    "risk-off": {"148070": 0.25, "153130": 0.25, "132030": 0.20, "261240": 0.15},
}
```

누락된 비중은 현금이다.

- [ ] **Step 6: 정책 테스트와 Ruff 통과 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_adaptive_strategies -v`

Run: `uvx ruff check adaptive_strategies.py tests/test_adaptive_strategies.py`

Expected: 지표와 네 정책 테스트 모두 통과.

- [ ] **Step 7: 변경 범위 검토**

Run: `git diff --check && git diff -- adaptive_strategies.py tests/test_adaptive_strategies.py`

Stop condition: 동일 가격 이력이 항상 동일 목표비중·레짐·근거를 반환하고 I/O나 포트폴리오 상태를 참조하지 않는다.

---

### Task 4: 공통 낙폭 제어 상태기계

**Files:**
- Create: `drawdown_guard.py`
- Create: `tests/test_drawdown_guard.py`

**Interfaces:**
- Produces: `GuardDecision(weights: dict[str, float], state: str, drawdown: float, event: Optional[dict])`
- Produces: `DrawdownGuard(risk_tickers, slowdown_drawdown=0.08, block_drawdown=0.10, cooldown_sessions=20, reentry_step=0.25, step_sessions=5)`
- Produces: `DrawdownGuard.update(equity: float, raw_weights: dict[str, float], regime: str, session_index: int) -> GuardDecision`
- Consumed by: `BacktestEngine.run_adaptive_strategy()`

- [ ] **Step 1: 상태 전이 실패 테스트 작성**

```python
class DrawdownGuardTest(unittest.TestCase):
    def setUp(self):
        self.guard = DrawdownGuard(
            risk_tickers={"069500", "143850", "133690"},
            slowdown_drawdown=0.08,
            block_drawdown=0.10,
            cooldown_sessions=20,
            reentry_step=0.25,
            step_sessions=5,
        )
        self.raw = {"143850": 0.70, "148070": 0.20}

    def test_eight_percent_drawdown_halves_only_risk_assets(self):
        self.guard.update(100.0, self.raw, "risk-on", 0)
        decision = self.guard.update(92.0, self.raw, "risk-on", 1)
        self.assertEqual(decision.state, "slowdown")
        self.assertEqual(decision.weights, {"143850": 0.35, "148070": 0.20})

    def test_ten_percent_drawdown_blocks_all_etfs(self):
        self.guard.update(100.0, self.raw, "risk-on", 0)
        decision = self.guard.update(89.5, self.raw, "risk-on", 1)
        self.assertEqual(decision.state, "blocked")
        self.assertEqual(decision.weights, {})

    def test_reentry_waits_twenty_sessions_and_scales_by_quarters(self):
        self.guard.update(100.0, self.raw, "risk-on", 0)
        self.guard.update(89.0, self.raw, "risk-on", 1)
        self.assertEqual(self.guard.update(89.0, self.raw, "neutral", 20).weights, {})
        self.assertAlmostEqual(sum(self.guard.update(89.2, self.raw, "neutral", 21).weights.values()), 0.25)
        self.assertAlmostEqual(sum(self.guard.update(89.5, self.raw, "neutral", 26).weights.values()), 0.50)

    def test_risk_off_or_new_low_resets_reentry(self):
        self.guard.update(100.0, self.raw, "risk-on", 0)
        self.guard.update(89.0, self.raw, "risk-on", 1)
        self.guard.update(89.2, self.raw, "neutral", 21)
        decision = self.guard.update(88.9, self.raw, "risk-off", 22)
        self.assertEqual(decision.state, "blocked")
        self.assertEqual(decision.weights, {})
```

- [ ] **Step 2: 모듈 부재로 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_drawdown_guard -v`

Expected: `ModuleNotFoundError: No module named 'drawdown_guard'`

- [ ] **Step 3: 상태와 불변식 구현**

`DrawdownGuard`는 `peak_equity`, `state`, `blocked_at`, `blocked_low`, `reentry_fraction`, `last_step_at`를 보관한다. 낙폭은 `1 - equity / peak_equity`로 계산한다.

```python
@dataclass(frozen=True)
class GuardDecision:
    weights: dict[str, float]
    state: str
    drawdown: float
    event: Optional[dict]
```

- 정상: 원 목표비중을 그대로 반환한다.
- 감속: `risk_tickers`만 0.5배하고 나머지 ETF 비중은 유지한다.
- 차단: 빈 비중을 반환해 100% 현금을 지시한다.
- 재진입: `session_index - blocked_at >= 20`, `regime != "risk-off"`, 새 저점 없음 조건을 모두 만족하면 전체 원 목표비중 합을 25%로 스케일한다.
- 5거래일마다 25%p를 추가하고 100%에서 정상으로 돌아간다.
- 재진입 중 새 저점 또는 `risk-off`이면 차단 시점을 현재 거래일로 갱신한다.
- 상태가 바뀔 때만 `event`를 만들고 이전·현재 상태, 낙폭, 적용 위험예산을 담는다.

- [ ] **Step 4: 상태기계 테스트와 Ruff 통과 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_drawdown_guard -v`

Run: `uvx ruff check drawdown_guard.py tests/test_drawdown_guard.py`

Expected: 모든 상태 전이 테스트 통과.

- [ ] **Step 5: 변경 범위 검토**

Run: `git diff --check && git diff -- drawdown_guard.py tests/test_drawdown_guard.py`

Stop condition: 상태 전이가 포트폴리오 엔진 없이 결정적으로 검증되고 차단 상태에서 ETF 비중은 항상 0이다.

---

### Task 5: 다음 거래일 목표비중 실행 엔진

**Files:**
- Modify: `backtester.py`
- Modify: `tests/test_backtester.py`

**Interfaces:**
- Consumes: `build_allocation()`, `DrawdownGuard.update()`, `get_strategy()`
- Produces: `BacktestEngine.run_adaptive_strategy(strategy_key: str, start_date: str, end_date: str) -> None`
- Produces: `BacktestEngine._actual_weights(prices: Dict[str, float]) -> Dict[str, float]`
- Produces result fields: `allocation_history`, `regime_history`, `drawdown_guard_events`, `data_quality`

- [ ] **Step 1: 다음날 시가 체결과 목표비중 실패 테스트 작성**

```python
class AdaptiveBacktestExecutionTest(unittest.TestCase):
    def test_previous_close_signal_executes_at_next_open(self):
        engine = adaptive_engine_with_prices(signal_close=110.0, next_open=120.0)
        engine.run_adaptive_strategy(
            "defensive_dual_momentum",
            start_date="2026-01-02",
            end_date="2026-01-05",
        )
        first_trade = engine.portfolio.trades[0]
        self.assertEqual(first_trade.entry_date, "2026-01-05")
        self.assertAlmostEqual(first_trade.entry_price, 120.12)

    def test_missing_open_defers_trade_without_close_fallback(self):
        engine = adaptive_engine_with_missing_execution_open()
        engine.run_adaptive_strategy("defensive_dual_momentum")
        self.assertEqual(engine.portfolio.trades, [])
        self.assertEqual(engine.execution_warnings[0]["reason"], "missing_open")

    def test_result_contains_allocation_regime_and_guard_history(self):
        engine = adaptive_engine_with_guard_trigger()
        engine.run_adaptive_strategy("price_regime_ensemble")
        results = engine.get_results()
        self.assertTrue(results["allocation_history"])
        self.assertTrue(results["regime_history"])
        self.assertTrue(results["drawdown_guard_events"])
        self.assertEqual(results["risk_control"]["max_drawdown_limit"], 10.0)
```

`adaptive_engine_with_prices()`는 워밍업 253거래일과 실행 2거래일을 생성하며, 신호일 종가와 다음 거래일 시가를 다르게 해 같은 날 종가 체결을 탐지한다.

- [ ] **Step 2: 새 엔진 메서드가 없어 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_backtester.AdaptiveBacktestExecutionTest -v`

Expected: `BacktestEngine`에 `run_adaptive_strategy`가 없어 실패.

- [ ] **Step 3: 목표비중 주문 실행기 구현**

`BacktestEngine`에 다음 내부 메서드를 추가한다.

```python
def _rebalance_to_target_weights(
    self,
    date: str,
    target_weights: Dict[str, float],
    previous_close_prices: Dict[str, float],
) -> None:
    open_prices = {}
    for ticker in self.price_data:
        price = self._price(ticker, date, "open")
        if self._has_valid_close(price):
            open_prices[ticker] = float(price)

    valuation_prices = dict(previous_close_prices)
    valuation_prices.update(open_prices)
    equity = self.portfolio.equity(valuation_prices)
    minimum_trade = equity * 0.01

    for ticker, position in list(self.portfolio.positions.items()):
        price = open_prices.get(ticker)
        if price is None:
            self.execution_warnings.append({"date": date, "ticker": ticker, "reason": "missing_open"})
            continue
        current_value = position["shares"] * price
        target_value = equity * target_weights.get(ticker, 0.0)
        excess = current_value - target_value
        if excess >= minimum_trade:
            shares = min(position["shares"], int(excess / price))
            if shares > 0:
                self.portfolio.sell(ticker, price, shares, date)

    for ticker, weight in target_weights.items():
        price = open_prices.get(ticker)
        if price is None:
            self.execution_warnings.append({"date": date, "ticker": ticker, "reason": "missing_open"})
            continue
        current = self.portfolio.positions.get(ticker)
        current_value = (current["shares"] * price) if current else 0.0
        shortfall = equity * weight - current_value
        if shortfall < minimum_trade:
            continue
        execution_price = price * (1 + self.cost_config.slippage_pct / 100)
        gross_unit_cost = execution_price * (1 + self.cost_config.commission_pct / 100)
        shares = int(min(shortfall, self.portfolio.cash) / gross_unit_cost)
        if shares > 0:
                self.portfolio.buy(ticker, price, shares, date, self.ticker_names.get(ticker, ticker))

def _actual_weights(self, prices: Dict[str, float]) -> Dict[str, float]:
    equity = self.portfolio.equity(prices)
    if equity <= 0:
        return {"cash": 1.0}
    weights = {
        ticker: position["shares"] * prices[ticker] / equity
        for ticker, position in self.portfolio.positions.items()
        if ticker in prices
    }
    weights["cash"] = self.portfolio.cash / equity
    return weights
```

정확한 규칙:

1. 시가가 유한한 양수가 아닌 자산은 그날 거래하지 않고 경고를 기록한다.
2. 주문 전 equity는 보유 종목별 당일 시가를 우선 사용하고, 시가가 없으면 전 거래일 종가로만 평가한다. 당일 종가는 주문 계산에 사용하지 않는다.
3. `target_value = equity * target_weight`로 계산한다.
4. 목표 초과 종목을 `int((current_value - target_value) / open_price)`만큼 먼저 매도한다.
5. 갱신된 현금으로 목표 미달 종목을 매수한다.
6. 목표 차이가 equity의 1% 미만이면 주문하지 않는다.
7. 기존 `Portfolio.buy/sell`을 사용해 편도 수수료·슬리피지를 한 번만 반영한다.

- [ ] **Step 4: 적응형 일별 실행 루프 구현**

`run_adaptive_strategy()`는 사용자가 지정한 실행일만 스냅샷으로 기록하되 모든 워밍업 데이터를 신호에 사용한다.

```python
def run_adaptive_strategy(self, strategy_key, start_date=None, end_date=None):
    spec = get_strategy(strategy_key)
    if spec.kind != "etf":
        raise ValueError(f"ETF 전략이 아닙니다: {strategy_key}")
    self._build_dates()
    execution_dates = [date for date in self.all_dates if (start_date is None or date >= start_date) and (end_date is None or date <= end_date)]
    date_indexes = {date: index for index, date in enumerate(self.all_dates)}
    guard = DrawdownGuard(risk_tickers={asset.ticker for asset in ETF_ASSETS.values() if asset.role == "risk"})
    raw_decision = None
    last_signal_month = None

    for session_index, date in enumerate(execution_dates):
        all_index = date_indexes[date]
        if all_index == 0:
            continue
        signal_date = self.all_dates[all_index - 1]
        signal_month = signal_date[:7]
        if raw_decision is None or signal_month != last_signal_month:
            histories = {
                ticker: [row["close"] for row in rows if row["date"] <= signal_date]
                for ticker, rows in self.price_data.items()
            }
            raw_decision = build_allocation(strategy_key, histories)
            last_signal_month = signal_month
            self.regime_history.append({"date": signal_date, "regime": raw_decision.regime, "evidence": raw_decision.evidence})

        previous_prices = self._last_known_prices(signal_date)
        previous_equity = self.portfolio.equity(previous_prices)
        guarded = guard.update(previous_equity, raw_decision.target_weights, raw_decision.regime, session_index)
        self._rebalance_to_target_weights(date, guarded.weights, previous_prices)
        close_prices = self._last_known_prices(date)
        self.portfolio.snapshot(date, close_prices)
        actual_weights = self._actual_weights(close_prices)
        self.allocation_history.append({"date": date, "target_weights": guarded.weights, "actual_weights": actual_weights, "cash_weight": actual_weights.get("cash", 0.0), "guard_state": guarded.state})
        if guarded.event is not None:
            self.drawdown_guard_events.append(guarded.event)
```

초기화 시 `allocation_history`, `regime_history`, `drawdown_guard_events`, `execution_warnings`, `data_quality`를 빈 값으로 둔다. `get_results()`는 이 필드와 다음 위험 제어 요약을 반환한다.

```python
{
    "max_drawdown_limit": 10.0,
    "max_drawdown_overshoot": max(0.0, abs(metrics["mdd"]) - 10.0),
}
```

- [ ] **Step 5: 집중·기존 엔진 회귀 검증**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_backtester.AdaptiveBacktestExecutionTest -v`

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_backtester -v`

Run: `uvx ruff check backtester.py tests/test_backtester.py`

Expected: 신규 실행 테스트와 기존 회계·벤치마크·스탑 테스트 모두 통과.

- [ ] **Step 6: 변경 범위 검토**

Run: `git diff --check && git diff -- backtester.py tests/test_backtester.py`

Stop condition: 신규 전략만 다음 거래일 시가 체결을 사용하고 기존 6개 전략 결과는 바뀌지 않는다.

---

### Task 6: Flask API·화면·CSV·일일 리포트 통합

**Files:**
- Modify: `app.py`
- Modify: `daily_report.py`
- Modify: `tests/test_app.py`
- Modify: `tests/test_daily_report.py`

**Interfaces:**
- Consumes: `STRATEGIES`, `strategy_groups()`, `is_etf_strategy()`, `ETF_ASSETS`, `run_strategy()`
- Produces: 기존 `/api/backtest/run`, `/api/backtest/status`, `/api/backtest/csv`, `/backtest`의 확장 계약

- [ ] **Step 1: API 입력·비용·필터 계약 실패 테스트 작성**

```python
    def test_etf_strategy_uses_kiwoom_cost_defaults(self):
        with patch.object(app_module.threading, "Thread") as thread:
            response = self.client.post(
                "/api/backtest/run",
                json={"strategy": "trend_risk_parity", "period": 24},
            )
        self.assertEqual(response.status_code, 200)
        args = thread.call_args.kwargs["args"]
        self.assertEqual(args[3:6], (0.10, 0.015, 0.0))

    def test_etf_strategy_rejects_stock_filters_and_nonzero_tax(self):
        for payload in (
            {"strategy": "trend_risk_parity", "scores": [3]},
            {"strategy": "trend_risk_parity", "items": ["nps"]},
            {"strategy": "trend_risk_parity", "tax": 0.20},
        ):
            with self.subTest(payload=payload):
                response = self.client.post("/api/backtest/run", json=payload)
                self.assertEqual(response.status_code, 400)
```

- [ ] **Step 2: ETF 데이터 로드·워밍업·디스패치 실패 테스트 작성**

`run_backtest_task()`를 mock StockDB로 직접 호출해 다음을 검증한다.

```python
    def test_etf_task_fetches_400_day_warmup_and_runs_adaptive_strategy(self):
        run_backtest_task(24, 100_000_000, "trend_risk_parity", 0.10, 0.015, 0.0)
        fetch = app_module.stock_db.ensure_adjusted_etf_data.call_args
        self.assertEqual(set(fetch.args[0]), {asset.ticker for asset in ETF_ASSETS.values()})
        requested_start = datetime.strptime(fetch.args[1], "%Y%m%d")
        performance_start = datetime(2026, 9, 5) - timedelta(days=24 * 30)
        self.assertGreaterEqual((performance_start - requested_start).days, 399)
        engine.run_adaptive_strategy.assert_called_once_with(
            "trend_risk_parity",
            start_date=ANY,
            end_date=ANY,
        )
```

- [ ] **Step 3: API 경계와 작업 분기 구현**

- `allowed_strategies` 하드코딩을 `set(STRATEGIES)`로 교체한다.
- 요청에 비용 필드가 없으면 기존 전략은 현재 기본값을 유지하고 ETF 전략만 `slippage=0.10`, `commission=0.015`, `tax=0.0`을 사용한다.
- ETF 전략에 비어 있지 않은 `scores` 또는 `items`, 0이 아닌 `tax`가 오면 작업 시작 전에 HTTP 400을 반환한다.
- `run_backtest_task()`는 ETF 전략이면 스크리닝 후보 계산을 건너뛰고 요청 시작일보다 400일 앞선 날짜부터 8개 ETF를 `ensure_adjusted_etf_data()`로 수집한다.
- `get_adjusted_prices_many()` 결과를 자산 표시명과 함께 엔진에 적재하고, 사용자 시작일·실제 가용기간·출처를 `engine.data_quality`에 기록한다.
- 기존·ETF 전략 모두 `strategy_runner.run_strategy()`로 실행한다.
- 결과 `config`에 `strategy_version=1`, `asset_universe`, `tax_model="etf_pre_tax"`를 기록한다.

- [ ] **Step 4: 화면 전략 그룹과 설정 전환 실패 테스트 작성**

```python
    def test_backtest_page_groups_etf_strategies_and_toggles_costs(self):
        template = self.client.get("/backtest").get_data(as_text=True)
        self.assertIn('<optgroup label="레짐·자산배분 전략">', template)
        self.assertIn('value="price_regime_ensemble"', template)
        self.assertIn('const ETF_STRATEGIES', template)
        self.assertIn("commission.value = '0.015'", template)
        self.assertIn("slippage.value = '0.10'", template)
        self.assertIn("tax.value = '0'", template)
        self.assertIn("filterConfig.disabled = isEtf", template)
```

- [ ] **Step 5: Jinja 기반 옵션과 ETF 설정 UI 구현**

`backtest_page()`는 `strategy_groups=strategy_groups()`를 전달한다.

```html
{% for group_name, strategies in strategy_groups %}
<optgroup label="{{ group_name }}">
{% for strategy in strategies %}
<option value="{{ strategy.key }}">{{ strategy.label }}</option>
{% endfor %}
</optgroup>
{% endfor %}
```

기존 필터 컨테이너는 `<fieldset id="filterConfig">`로 바꿔 `filterConfig.disabled = isEtf`가 내부 체크박스 전체에 실제 적용되도록 한다.

JavaScript에 `ETF_STRATEGIES` 배열을 서버 데이터에서 JSON으로 주입하고 `change` 이벤트에서 다음을 수행한다.

- ETF 전략: 점수·항목 필터 비활성화, `commission=0.015`, `slippage=0.10`, `tax=0`, tax 입력 비활성화, 요청 JSON에서 `scores/items` 제외.
- 기존 전략: 필터와 tax 입력 복원, 기존 비용값 복원.
- 설명 영역에 고정 ETF·세전 성과·10% 임계값의 갭 초과 가능성을 표시.

- [ ] **Step 6: 결과 카드와 CSV 실패 테스트 작성**

```python
    def test_etf_result_renders_regime_allocation_and_overshoot(self):
        template = self.client.get("/backtest").get_data(as_text=True)
        self.assertIn('id="regimeSummary"', template)
        self.assertIn('id="allocationBody"', template)
        self.assertIn('max_drawdown_overshoot', template)

    def test_etf_csv_contains_regime_allocation_and_tax_model(self):
        app_module.backtest_state["results"] = adaptive_result_fixture()
        response = self.client.get("/api/backtest/csv")
        text = response.get_data(as_text=True)
        self.assertIn("tax_model,etf_pre_tax", text)
        self.assertIn("regime", text)
        self.assertIn("target_weights", text)
        self.assertIn("guard_state", text)
```

- [ ] **Step 7: 결과 UI와 CSV 확장 구현**

- 결과 상단에 현재 레짐, 현재 현금비중, 최대 MDD 초과폭을 표시한다.
- `allocation_history`의 최신 행을 자산별 목표·실제 비중 표로 렌더링한다.
- `drawdown_guard_events`를 날짜·이전 상태·현재 상태·낙폭·조치 표로 렌더링한다.
- CSV 설정 섹션에 `tax_model`, `price_source`, `max_drawdown_limit`, `max_drawdown_overshoot`를 추가한다.
- CSV 일별 섹션에 날짜별 `regime`, JSON 직렬화한 `target_weights`, `actual_weights`, `guard_state`를 추가한다.

- [ ] **Step 8: 일일 리포트 카탈로그 동기화 구현**

`daily_report.py`의 로컬 `strategy_names`를 제거하고 `get_strategy(STRATEGY).label`을 사용한다. 기존 실행 분기 전체를 제거하고 `strategy_runner.run_strategy()`를 호출한다. 기본 `STRATEGY="composite"`는 유지한다. 일일 리포트에는 ETF 전용 가격 적재 단계가 없으므로 `is_etf_strategy(STRATEGY)`이면 실행 전에 `ValueError("일일 리포트는 ETF 전략을 지원하지 않습니다")`를 발생시켜 잘못된 데이터로 조용히 실행되지 않게 한다.

테스트는 모든 `STRATEGIES` 키에 표시명이 존재하며 `vol_trailing_stop_loss`가 `stop_loss_pct`를 전달하는지, ETF 전략 키가 명시적 오류로 거부되는지 검증한다.

- [ ] **Step 9: 집중·전체 통합 회귀 검증**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_app tests.test_daily_report -v`

Run: `uvx ruff check app.py daily_report.py tests/test_app.py tests/test_daily_report.py`

Expected: 신규 API·UI·CSV·리포트 테스트와 기존 스케줄러·캐시·필터 테스트 모두 통과.

- [ ] **Step 10: 변경 범위 검토**

Run: `git diff --check && git diff -- app.py daily_report.py tests/test_app.py tests/test_daily_report.py`

Stop condition: 전략명·허용 목록·화면 옵션이 중앙 카탈로그와 일치하고 ETF 전략만 고정 ETF·전용 비용 계약을 사용한다.

---

### Task 7: 재현 가능한 성과 검증과 문서화

**Files:**
- Create: `scripts/evaluate_adaptive_strategies.py`
- Create: `tests/test_adaptive_evaluation.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: `ETF_STRATEGY_KEYS`, `StockDB.get_adjusted_prices_many()`, `BacktestEngine.run_adaptive_strategy()`
- Produces: JSON 평가 보고서 `.omx/logs/adaptive-strategy-validation.json`
- Produces: 프로세스 종료코드 0(최소 2개 통과), 1(데이터/실행 오류), 2(2개 미만 통과)

- [ ] **Step 1: 성과 판정 순수함수 실패 테스트 작성**

```python
class AdaptiveEvaluationTest(unittest.TestCase):
    def test_strategy_passes_only_when_every_contract_is_met(self):
        metrics = {
            "annual_return": 6.0,
            "mdd": -9.8,
            "benchmark_mdd": -25.0,
            "positive_rolling_3y_ratio": 0.80,
            "max_drawdown_overshoot": 0.0,
        }
        self.assertTrue(evaluate_contract(metrics)["passed"])
        metrics["positive_rolling_3y_ratio"] = 0.70
        self.assertFalse(evaluate_contract(metrics)["passed"])

    def test_portfolio_completion_requires_two_passes(self):
        self.assertTrue(completion_passed([True, True, False, False]))
        self.assertFalse(completion_passed([True, False, False, False]))
```

- [ ] **Step 2: 평가 스크립트 부재로 실패하는지 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_adaptive_evaluation -v`

Expected: 평가 함수 import 실패.

- [ ] **Step 3: 평가 함수와 CLI 구현**

스크립트는 `--db`, `--months`, `--output` 인자를 받고 기본값을 각각 `stock_data.duckdb`, 가능한 최대 공통기간, `.omx/logs/adaptive-strategy-validation.json`으로 둔다.

```python
def evaluate_contract(metrics: dict) -> dict:
    checks = {
        "cagr_positive": metrics["annual_return"] > 0,
        "mdd_better_than_kospi": abs(metrics["mdd"]) < abs(metrics["benchmark_mdd"]),
        "rolling_positive": metrics["positive_rolling_3y_ratio"] >= 0.75,
        "mdd_limit": abs(metrics["mdd"]) <= 10.0 or metrics["max_drawdown_overshoot"] <= 1.5,
    }
    return {"passed": all(checks.values()), "checks": checks}


def completion_passed(strategy_passes: list[bool]) -> bool:
    return sum(strategy_passes) >= 2
```

각 전략에 대해 전체 공통기간, 마지막 30% 표본외, 36개월 롤링, `2020-02-01~2020-06-30`, `2022-01-01~2022-12-31`, 최신 12개월을 평가한다. 거래비용은 수수료 0.015%, 슬리피지 0.10%, 거래세 0으로 고정한다.

JSON은 실행시각, 데이터 실제 범위, ETF 유니버스, 비용, 전략별 전체/표본외/롤링/충격구간 메트릭, 개별 체크와 전체 완료 여부를 담는다.

- [ ] **Step 4: 판정 테스트 통과 확인**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest tests.test_adaptive_evaluation -v`

Run: `uvx ruff check scripts/evaluate_adaptive_strategies.py tests/test_adaptive_evaluation.py`

Expected: 판정 경계 테스트 통과.

- [ ] **Step 5: README 갱신**

README에 다음 내용을 추가한다.

- 지원 전략 수를 10개로 변경하고 신규 4개 키·표시명·핵심 규칙을 표에 추가한다.
- 고정 ETF 유니버스와 개별주 필터 미적용을 설명한다.
- 전일 종가 신호·다음날 시가 체결, 8% 감속·10% 현금화·20일 재진입을 설명한다.
- 키움 온라인 ETF 편도 0.015%, 슬리피지 편도 0.10%, ETF 거래세 0%를 명시한다.
- 기타 ETF 보유기간과세를 제외한 세전 결과임을 명시한다.
- 다음 평가 명령과 JSON 결과 위치를 추가한다.

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python scripts/evaluate_adaptive_strategies.py`

- [ ] **Step 6: 전체 정적·회귀 검증**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m unittest discover -s tests -v`

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -m py_compile app.py adaptive_strategies.py backtester.py daily_report.py drawdown_guard.py strategy_catalog.py stock_db.py scripts/evaluate_adaptive_strategies.py`

Run: `uvx ruff check app.py adaptive_strategies.py backtester.py daily_report.py drawdown_guard.py strategy_catalog.py stock_db.py scripts/evaluate_adaptive_strategies.py tests`

Run: `git diff --check`

Expected: 모든 테스트, 컴파일, Ruff, diff 검사 통과.

- [ ] **Step 7: 실제 ETF 데이터와 성과 평가 실행**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python scripts/evaluate_adaptive_strategies.py --db stock_data.duckdb --output .omx/logs/adaptive-strategy-validation.json`

Expected:

- 8개 ETF 조정가격의 실제 공통 시작일과 종료일이 보고된다.
- 네 전략의 전체·표본외·롤링·충격구간 메트릭이 기록된다.
- 최소 2개 전략이 모든 통과 기준을 만족해 종료코드 0이다.

종료코드 2이면 공통 위험예산과 자산 상한만 한 차례 조정한다. 허용 조정 범위는 감속 임계값 7~8%, 감속 배율 25~50%, 주식 합계 상한 40~60%다. 개별 전략의 룩백이나 테스트 구간별 파라미터는 바꾸지 않는다. 조정 전후 값을 JSON에 모두 남기고 전체 검증을 다시 실행한다.

- [ ] **Step 8: Flask 스모크 검증**

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -c "import app; client=app.app.test_client(); page=client.get('/backtest'); assert page.status_code == 200; text=page.get_data(as_text=True); assert 'price_regime_ensemble' in text; print('backtest_page_ok')"`

Run: `uv run --isolated --managed-python --python 3.11 --with-requirements requirements.txt python -c "import app; client=app.app.test_client(); response=client.post('/api/backtest/run', json={'strategy':'trend_risk_parity','period':24}); assert response.status_code == 200, response.get_data(as_text=True); print(response.get_json())"`

백그라운드 실행이 끝날 때까지 `/api/backtest/status`를 제한적으로 폴링하고 `status == "done"`, `config.tax_model == "etf_pre_tax"`, `allocation_history` 비어 있지 않음을 확인한다. 외부 데이터 장애면 오류 메시지와 마지막 성공 검증을 보고하고 성과 통과를 주장하지 않는다.

- [ ] **Step 9: 최종 변경 검토**

Run: `git status --short && git diff --stat && git diff --check`

Stop condition: 코드·테스트·문서가 설계 문서의 모든 계약을 구현하고, 정적·회귀 검증이 통과하며, 실제 평가에서 최소 2개 전략이 승인된 성과 기준을 충족한다.
