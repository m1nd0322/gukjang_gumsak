import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import daily_refresh
from daily_refresh import refresh_is_due
from scripts.install_daily_refresh_launch_agent import build_launch_agent

KST = ZoneInfo("Asia/Seoul")


class DailyRefreshDueTest(unittest.TestCase):
    def test_refresh_is_due_only_after_seven_when_today_has_not_run(self):
        cases = (
            (
                "2026-08-14 07:00:00",
                datetime(2026, 8, 15, 6, 59, tzinfo=KST),
                False,
            ),
            (
                "2026-08-14 07:00:00",
                datetime(2026, 8, 15, 7, 0, tzinfo=KST),
                True,
            ),
            (
                "2026-08-15 00:09:33",
                datetime(2026, 8, 15, 7, 0, tzinfo=KST),
                True,
            ),
            (
                "2026-08-15 07:00:00",
                datetime(2026, 8, 15, 12, 0, tzinfo=KST),
                False,
            ),
        )

        for last_updated, now, expected in cases:
            with self.subTest(last_updated=last_updated, now=now):
                self.assertEqual(refresh_is_due(last_updated, now), expected)

    def test_missing_or_invalid_update_time_is_due_after_seven(self):
        now = datetime(2026, 8, 15, 7, 0, tzinfo=KST)

        self.assertTrue(refresh_is_due(None, now))
        self.assertTrue(refresh_is_due("invalid", now))

    def test_run_before_seven_exits_without_contacting_the_dashboard(self):
        now = datetime(2026, 8, 15, 6, 59, tzinfo=KST)

        with patch.object(
            daily_refresh,
            "_dashboard_status",
            side_effect=AssertionError("07:00 전에는 서버를 조회하면 안 됩니다"),
        ):
            result = daily_refresh.run(now=now)

        self.assertEqual(result, 0)


class LaunchAgentConfigTest(unittest.TestCase):
    def test_agent_runs_at_login_and_every_day_at_seven(self):
        config = build_launch_agent(
            Path("/tmp/gukjang_gumsak"),
            Path("/opt/homebrew/bin/uv"),
        )

        self.assertTrue(config["RunAtLoad"])
        self.assertEqual(
            config["StartCalendarInterval"],
            {"Hour": 7, "Minute": 0},
        )
        self.assertEqual(
            config["ProgramArguments"],
            [
                "/opt/homebrew/bin/uv",
                "run",
                "--isolated",
                "--managed-python",
                "--python",
                "3.11",
                "--with-requirements",
                "/tmp/gukjang_gumsak/requirements.txt",
                "python",
                "/tmp/gukjang_gumsak/daily_refresh.py",
            ],
        )


if __name__ == "__main__":
    unittest.main()
