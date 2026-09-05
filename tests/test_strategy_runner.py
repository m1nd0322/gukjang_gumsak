import unittest
from unittest.mock import Mock

from strategy_runner import run_strategy


class StrategyRunnerTest(unittest.TestCase):
    def test_run_strategy_dispatches_each_legacy_strategy_once(self):
        cases = (
            (
                "equal_weight",
                "run_equal_weight",
                (["005930", "000660"], "2024-01-01", "2024-12-31"),
                {},
            ),
            (
                "rebalance",
                "run_rebalance",
                (["005930", "000660"], "2024-01-01", "2024-12-31"),
                {"period": 20},
            ),
            (
                "vol_trailing_stop",
                "run_volatility_trailing_stop",
                (["005930", "000660"], "2024-01-01", "2024-12-31"),
                {
                    "lookback": 20,
                    "stop_pct": -10.0,
                    "cooldown": 5,
                    "reentry": True,
                },
            ),
            (
                "vol_trailing_stop_loss",
                "run_volatility_trailing_stop",
                (["005930", "000660"], "2024-01-01", "2024-12-31"),
                {
                    "lookback": 20,
                    "stop_pct": -10.0,
                    "cooldown": 5,
                    "reentry": True,
                    "stop_loss_pct": 6.5,
                },
            ),
            (
                "ma_filter",
                "run_ma_filter",
                (["005930", "000660"], "2024-01-01", "2024-12-31"),
                {"ma_period": 20, "rebalance_period": 5},
            ),
            (
                "composite",
                "run_composite",
                (["005930", "000660"], "2024-01-01", "2024-12-31"),
                {
                    "ma_period": 20,
                    "lookback": 20,
                    "stop_pct": -8.0,
                    "cooldown": 5,
                    "rebalance_period": 10,
                },
            ),
        )

        for key, method_name, args, kwargs in cases:
            with self.subTest(key=key):
                engine = Mock()

                run_strategy(
                    engine,
                    key,
                    ["005930", "000660"],
                    start_date="2024-01-01",
                    end_date="2024-12-31",
                    stop_loss_pct=6.5,
                )

                getattr(engine, method_name).assert_called_once_with(*args, **kwargs)

    def test_run_strategy_dispatches_etf_strategies_by_key(self):
        for key in (
            "defensive_dual_momentum",
            "multi_asset_trend_rotation",
            "trend_risk_parity",
            "price_regime_ensemble",
        ):
            with self.subTest(key=key):
                engine = Mock()

                run_strategy(
                    engine,
                    key,
                    ["005930", "000660"],
                    start_date="2024-01-01",
                    end_date="2024-12-31",
                )

                engine.run_adaptive_strategy.assert_called_once_with(
                    key,
                    start_date="2024-01-01",
                    end_date="2024-12-31",
                )

    def test_run_strategy_rejects_unknown_strategy(self):
        engine = Mock()

        with self.assertRaises(KeyError):
            run_strategy(engine, "missing", ["005930"])
