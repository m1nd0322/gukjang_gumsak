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


if __name__ == "__main__":
    unittest.main()
