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
                }
            ],
            {"nps_count": 1, "score_1": 1},
            {"metrics": {}, "stock_performance": []},
            {},
        )

        self.assertIn("국민연금 신규/추가매수: 1종목", message)


if __name__ == "__main__":
    unittest.main()
