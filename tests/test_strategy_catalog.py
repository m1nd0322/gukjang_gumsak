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
