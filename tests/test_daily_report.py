import unittest
from unittest.mock import patch

import daily_report
from screening import ScreeningDataError


class DailyReportSourceValidationTest(unittest.TestCase):
    @patch("daily_report.send_telegram")
    @patch(
        "daily_report.fetch_all_data",
        side_effect=ScreeningDataError("순매수전환: broken"),
    )
    def test_aborts_when_a_required_screening_source_fails(
        self, _fetch_all_data, send_telegram
    ):
        with self.assertRaises(SystemExit) as raised:
            daily_report.main()

        self.assertEqual(raised.exception.code, 1)
        _fetch_all_data.assert_called_once_with(require_all=True)
        send_telegram.assert_called_once()
        self.assertIn("순매수전환", send_telegram.call_args.args[0])

    def test_message_uses_nps_buy_signal_label(self):
        message = daily_report.format_telegram_message(
            [
                {
                    "종목명": "A",
                    "종합점수": 1,
                    "출처": "국민연금 신규/추가매수",
                    "[연금]매수구분": "추가매수",
                    "[연금]매수일": "2026-06-30",
                    "[연금]만료일": "2026-09-30",
                }
            ],
            {"nps_count": 1, "score_1": 1},
            {"metrics": {}, "strategy_stock_performance": []},
            {},
        )

        self.assertIn("국민연금 신규/추가매수: 1종목", message)
        self.assertIn("추가매수 2026-06-30", message)
        self.assertIn("만료 2026-09-30", message)

    def test_message_escapes_external_stock_values(self):
        message = daily_report.format_telegram_message(
            [
                {
                    "종목명": "<b>위조</b>",
                    "종합점수": 1,
                    "출처": "국민연금 <i>위조</i>",
                }
            ],
            {"nps_count": 1, "score_1": 1},
            {"metrics": {}, "strategy_stock_performance": []},
            {},
        )

        self.assertNotIn("<b>위조</b>", message)
        self.assertIn("&lt;b&gt;위조&lt;/b&gt;", message)
        self.assertIn("&lt;i&gt;위조&lt;/i&gt;", message)

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
        self.assertIn(
            "📈 &lt;b&gt;수익종목&lt;/b&gt;: +1,234원 (+5.50%)",
            message,
        )
        self.assertIn("📉 손실종목: -500원 (-2.00%)", message)
        self.assertIn("📈 보합종목: 0원 (0.00%)", message)
        self.assertLess(message.index("수익종목"), message.index("손실종목"))
        self.assertNotIn("▸ 개별 종목 수익률", message)
        self.assertNotIn("(MDD", message)

    @patch("daily_report.StockDB")
    @patch("daily_report.send_telegram")
    @patch(
        "daily_report.calculate_scores",
        return_value=(
            [{"종목명": "A", "종합점수": 1, "출처": "연간실적호전"}],
            {"score_3": 0, "score_2": 0, "score_1": 1},
        ),
    )
    @patch("daily_report.fetch_all_data", return_value=([], [], []))
    def test_persists_screening_results_before_no_high_score_exit(
        self,
        _fetch_all_data,
        _calculate_scores,
        _send_telegram,
        stock_db_class,
    ):
        stock_db_class.return_value.replace_screening_results.return_value = 1

        with self.assertRaises(SystemExit) as raised:
            daily_report.main()

        self.assertEqual(raised.exception.code, 0)
        stock_db_class.return_value.replace_screening_results.assert_called_once_with(
            [{"종목명": "A", "종합점수": 1, "출처": "연간실적호전"}]
        )

    @patch("daily_report.StockDB")
    @patch("daily_report.send_telegram")
    @patch(
        "daily_report.calculate_scores",
        return_value=(
            [{"종목명": "A", "종합점수": 1, "출처": "연간실적호전"}],
            {"score_3": 0, "score_2": 0, "score_1": 1},
        ),
    )
    @patch("daily_report.fetch_all_data", return_value=([], [], []))
    def test_aborts_when_screening_results_cannot_be_persisted(
        self,
        _fetch_all_data,
        _calculate_scores,
        send_telegram,
        stock_db_class,
    ):
        stock_db_class.return_value.replace_screening_results.side_effect = (
            RuntimeError("duckdb write failed")
        )

        with self.assertRaises(SystemExit) as raised:
            daily_report.main()

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("DuckDB", send_telegram.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
