import unittest

from backtester import BacktestEngine, CostConfig, Portfolio


def price(date, close):
    return {
        "date": date,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1,
    }


class BacktestAccountingTest(unittest.TestCase):
    def test_opening_cost_is_in_total_return_drawdown_and_benchmark_base(self):
        engine = BacktestEngine(initial_capital=1_000, commission_pct=1.0)
        engine.add_price_data(
            "AAA",
            [price("2026-01-02", 100), price("2026-01-05", 100)],
            name="테스트",
        )
        engine.set_benchmark(
            [
                {"date": "2026-01-02", "close": 100},
                {"date": "2026-01-05", "close": 100},
            ]
        )

        engine.run_equal_weight(["AAA"])
        results = engine.get_results()

        self.assertEqual(results["metrics"]["final_equity"], 991)
        self.assertEqual(results["metrics"]["total_return"], -0.9)
        self.assertEqual(results["metrics"]["mdd"], -0.9)
        self.assertEqual(results["benchmark"]["curve"][0]["equity"], 1_000)

    def test_fifo_partial_sale_closes_every_lot_and_reconciles_cash(self):
        portfolio = Portfolio(10_000, CostConfig(commission_pct=1.0))
        portfolio.buy("AAA", 100, 10, "2026-01-02", "테스트")
        portfolio.buy("AAA", 100, 10, "2026-01-05", "테스트")

        sold = portfolio.sell("AAA", 110, 15, "2026-01-06")

        self.assertEqual(sold, 15)
        closed = [trade for trade in portfolio.trades if trade.status == "closed"]
        opened = [trade for trade in portfolio.trades if trade.status == "open"]
        self.assertEqual([trade.shares for trade in closed], [10, 5])
        self.assertEqual([trade.shares for trade in opened], [5])
        self.assertAlmostEqual(sum(trade.pnl for trade in closed), 118.5)
        self.assertAlmostEqual(portfolio.cash, 9_613.5)
        self.assertEqual(portfolio.positions["AAA"]["shares"], 5)

        portfolio.sell("AAA", 110, 5, "2026-01-07")

        self.assertNotIn("AAA", portfolio.positions)
        self.assertTrue(all(trade.status == "closed" for trade in portfolio.trades))
        self.assertEqual(sum(trade.shares for trade in portfolio.trades), 20)
        self.assertAlmostEqual(sum(trade.pnl for trade in portfolio.trades), 158.0)
        self.assertAlmostEqual(portfolio.cash, 10_158.0)


if __name__ == "__main__":
    unittest.main()
