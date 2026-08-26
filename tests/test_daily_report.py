import time
import unittest
from unittest.mock import patch

import pandas as pd

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


def yfinance_frame(closes, volumes=None):
    index = pd.to_datetime([f"2026-08-{day:02d}" for day in range(3, 3 + len(closes))])
    if volumes is None:
        volumes = [1_000] * len(closes)
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes,
            "Low": closes,
            "Close": closes,
            "Volume": volumes,
        },
        index=index,
    )


class CollectPriceDataTest(unittest.TestCase):
    def test_falls_back_to_kq_when_ks_returns_empty_rows(self):
        def fake_download(symbol, **kwargs):
            if symbol.endswith(".KS"):
                return pd.DataFrame(
                    columns=["Open", "High", "Low", "Close", "Volume"]
                )
            return yfinance_frame([50_000, 51_000])

        with patch.object(daily_report.yf, "download", side_effect=fake_download):
            prices, used_symbol, last_error = daily_report._download_price_rows(
                "000250", "삼천당", "2026-08-01", "2026-08-31"
            )

        self.assertIsNone(last_error)
        self.assertEqual(used_symbol, "000250.KQ")
        self.assertEqual(len(prices), 2)

    def test_drops_nan_close_rows_and_zeroes_nan_volume(self):
        frame = yfinance_frame([100.0, float("nan"), 110.0], [500, 700, None])

        with patch.object(daily_report.yf, "download", return_value=frame):
            prices, _, last_error = daily_report._download_price_rows(
                "005930", "삼성전자", "2026-08-01", "2026-08-31"
            )

        self.assertIsNone(last_error)
        self.assertEqual(
            [row["date"] for row in prices],
            ["2026-08-03", "2026-08-05"],
        )
        self.assertEqual(prices[0]["volume"], 500)
        self.assertEqual(prices[1]["volume"], 0)

    def test_collect_keeps_matched_order_even_when_completion_reverses(self):
        def fake_download(symbol, **kwargs):
            # 첫 종목만 느리게 끝나도 결과 순서는 matched 순서를 유지한다.
            if symbol.startswith("111111"):
                time.sleep(0.2)
                return yfinance_frame([10.0, 11.0])
            return yfinance_frame([20.0, 21.0])

        matched = {"111111": "느린종목", "222222": "빠른종목"}
        with patch.object(daily_report.yf, "download", side_effect=fake_download):
            outcomes = daily_report.collect_price_data(
                matched, "2026-08-01", "2026-08-31", max_workers=2
            )

        self.assertEqual(list(outcomes), ["111111", "222222"])
        self.assertIsNotNone(outcomes["111111"][0])
        self.assertIsNotNone(outcomes["222222"][0])

    def test_collect_records_failures_without_losing_other_tickers(self):
        calls = []

        def fake_download(symbol, **kwargs):
            calls.append(symbol)
            if symbol.startswith("333333"):
                raise RuntimeError("일시적 429")
            return yfinance_frame([30.0, 31.0])

        matched = {"333333": "고장종목", "444444": "정상종목"}
        with patch.object(daily_report.yf, "download", side_effect=fake_download):
            outcomes = daily_report.collect_price_data(matched, "2026-08-01", "2026-08-31")

        # 고장 종목은 .KS/.KQ 두 번 모두 시도된다.
        self.assertIn("333333.KS", calls)
        self.assertIn("333333.KQ", calls)
        self.assertIsNone(outcomes["333333"][0])
        self.assertIsInstance(outcomes["333333"][2], RuntimeError)
        self.assertIsNotNone(outcomes["444444"][0])


if __name__ == "__main__":
    unittest.main()
