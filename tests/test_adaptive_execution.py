import csv
import io
import math
import unittest
from datetime import date, timedelta
from unittest.mock import patch

from adaptive_strategies import AllocationDecision, momentum
from backtester import BacktestEngine
from drawdown_guard import DrawdownGuard
from strategy_catalog import ETF_ASSETS
from strategy_export import write_adaptive_csv


def engine_fixture(count=290):
    engine = BacktestEngine(initial_capital=100_000, commission_pct=0.015,
                            slippage_pct=0.10, tax_pct=0)
    days = []
    day = date(2024, 1, 1)
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day.isoformat())
        day += timedelta(days=1)
    for asset in ETF_ASSETS.values():
        rows = [{'date': d, 'open': 100.0, 'high': 110.0, 'low': 99.0,
                     'close': 100 + i * 0.01, 'volume': 1000} for i, d in enumerate(days)]
        engine.add_price_data(asset.ticker, rows, asset.name)
    return engine, days


class DrawdownGuardTest(unittest.TestCase):
    def test_throttle_cash_cooldown_and_staged_recovery(self):
        guard = DrawdownGuard(100, base_risk_budget=1.0)
        weights = {'069500': .4, '153130': .5}
        self.assertEqual(guard.apply('a', 92, weights, 'neutral')['069500'], .2)
        self.assertEqual(guard.apply('b', 90, weights, 'neutral'), {})
        for i in range(19):
            self.assertEqual(guard.apply(str(i), 90, weights, 'neutral'), {})
        self.assertEqual(guard.apply('20', 90, weights, 'neutral')['069500'], .1)
        for i in range(15):
            guard.apply(str(i), 90, weights, 'neutral')
        self.assertEqual(guard.state, 'normal')
        self.assertEqual(guard.peak, 90)

    def test_reentry_new_low_or_risk_off_blocks_again(self):
        for equity, regime in [(89, 'neutral'), (90, 'risk-off')]:
            guard = DrawdownGuard(100, state='reentry', budget=.25, recovery_low=90)
            self.assertEqual(guard.apply('x', equity, {'069500': .4}, regime), {})
            self.assertEqual(guard.state, 'cash')


class AdaptiveExecutionTest(unittest.TestCase):
    def test_first_day_uses_prior_close_and_executes_open_then_marks_close(self):
        engine, days = engine_fixture()
        seen = []

        def decision(key, histories):
            seen.append(histories['069500'][-1])
            return AllocationDecision({'069500': .4}, 'neutral', {})

        with patch('backtester.build_allocation', side_effect=decision):
            engine.run_adaptive_strategy('trend_risk_parity', days[253], days[254])
        self.assertAlmostEqual(seen[0], 102.52)
        self.assertEqual(engine.portfolio.trades[0].entry_date, days[253])
        self.assertEqual(engine.portfolio.trades[0].entry_price, 100)
        self.assertAlmostEqual(engine.portfolio.equity_history[0]['equity'],
                               100_000 - 200 * 100.1 * 1.00015 + 200 * 102.53)
        self.assertEqual(len(engine.portfolio.equity_history), 2)

    def test_monthly_partial_reduction_and_no_daily_rebalance(self):
        engine, days = engine_fixture(320)
        calls = []

        def decision(key, histories):
            if key == 'trend_risk_parity':
                calls.append(len(histories['069500']))
                return AllocationDecision({'069500': .4 if len(calls) == 1 else .2}, 'neutral', {})
            return AllocationDecision({}, 'neutral', {})

        with patch('backtester.build_allocation', side_effect=decision):
            engine.run_adaptive_strategy('trend_risk_parity', days[253], days[-1])
        self.assertEqual(len(calls), len({d[:7] for d in days[253:]}))
        self.assertTrue(any(t.status == 'closed' for t in engine.portfolio.trades))
        self.assertLess(engine.portfolio.positions['069500']['shares'], 400)

    def test_missing_open_is_deferred_and_five_missing_sessions_fail(self):
        engine, days = engine_fixture()
        engine._price_idx['069500'][days[253]]['open'] = None
        with patch('backtester.build_allocation', return_value=AllocationDecision({'069500': .4}, 'neutral', {})):
            engine.run_adaptive_strategy('trend_risk_parity', days[253], days[254])
        self.assertEqual(engine.portfolio.trades[0].entry_date, days[254])
        self.assertEqual(len(engine.data_quality['deferred_orders']), 1)
        engine, days = engine_fixture()
        for d in days[253:258]:
            engine._price_idx['069500'][d]['open'] = None
        with self.assertRaisesRegex(ValueError, '5거래일'):
            engine.run_adaptive_strategy('trend_risk_parity', days[253], days[258])

    def test_missing_warmup_fails(self):
        engine, days = engine_fixture()
        with self.assertRaisesRegex(ValueError, '253거래일'):
            engine.run_adaptive_strategy('trend_risk_parity', days[252], days[-1])

    def test_nan_indicator_is_rejected(self):
        self.assertIsNone(momentum([100] * 252 + [math.nan]))

    def test_result_csv_contains_guard_and_allocations(self):
        engine, days = engine_fixture()
        engine.run_adaptive_strategy('price_regime_ensemble', days[253], days[255])
        results = engine.get_results()
        output = io.StringIO()
        write_adaptive_csv(csv.writer(output), results)
        self.assertIn('목표비중', output.getvalue())
        self.assertEqual(results['cost_summary']['tax'], 0)
        self.assertEqual(results['max_drawdown_limit'], 10)


class AdaptiveApiTest(unittest.TestCase):
    def test_api_rejects_stock_filters_and_nonzero_etf_tax(self):
        import app as module
        client = module.app.test_client()
        with patch.dict(module.backtest_state, status='idle'), patch.object(module.threading, 'Thread') as thread:
            for extra in ({'scores': []}, {'items': []}, {'tax': .2}):
                response = client.post('/api/backtest/run', json={'strategy': 'trend_risk_parity', **extra})
                self.assertEqual(response.status_code, 400)
            thread.assert_not_called()

    def test_etf_api_defaults_and_rendered_catalog(self):
        import app as module
        client = module.app.test_client()
        with patch.dict(module.backtest_state, status='idle'), patch.object(module.threading, 'Thread') as thread:
            response = client.post('/api/backtest/run', json={'strategy': 'trend_risk_parity'})
            self.assertEqual(response.status_code, 200)
            args = thread.call_args.kwargs['args']
            self.assertEqual(args[3:6], (.10, .015, 0))
        page = client.get('/backtest').get_data(as_text=True)
        for asset_strategy in ('defensive_dual_momentum', 'multi_asset_trend_rotation',
                               'trend_risk_parity', 'price_regime_ensemble'):
            self.assertIn(f'value="{asset_strategy}" data-etf=1', page)
        self.assertIn('보유기간과세', page)


class AllocationCapsTest(unittest.TestCase):
    def test_capped_weights_preserve_remaining_inverse_volatility_ratios(self):
        from adaptive_strategies import _normalize_with_caps
        weights = _normalize_with_caps({'a': .7, 'b': .2, 'c': .1},
                                       {'a': .4, 'b': .4, 'c': .4})
        self.assertAlmostEqual(weights['a'], .4)
        self.assertAlmostEqual(weights['b'], .4)
        self.assertAlmostEqual(weights['c'], .2)
        self.assertAlmostEqual(sum(weights.values()), 1)
