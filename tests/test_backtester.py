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
            sum(
                row["total_pnl"]
                for row in results["strategy_stock_performance"]
            ),
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

        closed = [
            trade for trade in engine.portfolio.trades
            if trade.status == "closed"
        ]
        self.assertEqual([trade.exit_date for trade in closed], ["2026-01-04"])
        self.assertNotIn("AAA", engine.portfolio.positions)

    def test_custom_stop_loss_changes_exit_date(self):
        cases = (
            (5.0, [100, 94, 92], "2026-01-03"),
            (8.0, [100, 94, 92], "2026-01-04"),
        )
        for stop_loss_pct, closes, expected_date in cases:
            with self.subTest(stop_loss_pct=stop_loss_pct):
                engine = self.run_strategy(
                    closes,
                    stop_loss_pct=stop_loss_pct,
                )
                closed = [
                    trade for trade in engine.portfolio.trades
                    if trade.status == "closed"
                ]
                self.assertEqual(
                    [trade.exit_date for trade in closed],
                    [expected_date],
                )

    def test_trailing_stop_still_sells_a_profitable_position(self):
        engine = self.run_strategy([100, 120, 108], stop_loss_pct=7.0)

        closed = [
            trade for trade in engine.portfolio.trades
            if trade.status == "closed"
        ]
        self.assertEqual([trade.exit_date for trade in closed], ["2026-01-04"])
        self.assertGreater(closed[0].pnl, 0)

    def test_none_stop_loss_preserves_legacy_trailing_only_behavior(self):
        engine = self.run_strategy([100, 93], stop_loss_pct=None)

        self.assertIn("AAA", engine.portfolio.positions)
        self.assertTrue(
            all(trade.status == "open" for trade in engine.portfolio.trades)
        )

    def test_stop_loss_reentry_waits_for_five_complete_trading_days(self):
        engine = self.run_strategy(
            [100, 93, 93, 93, 93, 93, 93, 93],
            stop_loss_pct=7.0,
        )

        self.assertEqual(len(engine.portfolio.trades), 2)
        self.assertEqual(engine.portfolio.trades[0].exit_date, "2026-01-03")
        self.assertEqual(engine.portfolio.trades[1].entry_date, "2026-01-09")
        self.assertEqual(engine.portfolio.trades[1].status, "open")


if __name__ == "__main__":
    unittest.main()
