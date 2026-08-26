import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import daily_refresh
from daily_refresh import refresh_is_due
from scripts.install_daily_refresh_launch_agent import (
    build_db_backup_agent,
    build_launch_agent,
    build_web_launch_agent,
)

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

    def test_web_agent_keeps_the_dashboard_running(self):
        config = build_web_launch_agent(
            Path("/tmp/gukjang_gumsak"),
            Path("/opt/homebrew/bin/uv"),
        )

        self.assertTrue(config["RunAtLoad"])
        self.assertTrue(config["KeepAlive"])
        self.assertEqual(
            config["ProgramArguments"][-2:],
            ["python", "/tmp/gukjang_gumsak/app.py"],
        )


class DbBackupAgentConfigTest(unittest.TestCase):
    def test_backup_agent_runs_at_login_and_every_sunday_morning(self):
        config = build_db_backup_agent(
            Path("/tmp/gukjang_gumsak"),
            Path("/opt/homebrew/bin/uv"),
        )

        self.assertTrue(config["RunAtLoad"])
        self.assertEqual(config["Label"], "com.songhear.gukjang-gumsak.db-backup")
        self.assertEqual(
            config["StartCalendarInterval"],
            {"Weekday": 0, "Hour": 6, "Minute": 0},
        )
        self.assertNotIn("KeepAlive", config)
        self.assertEqual(
            config["ProgramArguments"][-2:],
            [
                "python",
                "/tmp/gukjang_gumsak/scripts/backup_stock_db.py",
            ],
        )
        self.assertTrue(
            config["StandardOutPath"].endswith("db-backup.log"),
        )


class DashboardSupervisorTest(unittest.TestCase):
    def test_already_loading_waits_for_completion_before_reporting_success(self):
        now = datetime(2026, 8, 15, 7, 0, tzinfo=KST)
        responses = [
            {"status": "loading", "last_updated": "2026-08-14 07:00:00"},
            {"status": "done", "last_updated": "2026-08-15 07:00:20"},
        ]

        with (
            patch.object(
                daily_refresh,
                "_dashboard_status",
                side_effect=responses,
            ),
            patch.object(
                daily_refresh,
                "_request_dashboard_refresh",
                return_value={"status": "already_loading"},
            ) as request_refresh,
            patch.object(daily_refresh.time, "sleep"),
        ):
            result = daily_refresh.run(now=now)

        self.assertEqual(result, 0)
        request_refresh.assert_called_once()

    def test_failed_refresh_is_requested_again_until_data_is_fresh(self):
        now = datetime(2026, 8, 15, 7, 0, tzinfo=KST)
        stale = {"status": "done", "last_updated": "2026-08-14 07:00:00"}
        fresh = {
            "status": "done",
            "last_updated": "2026-08-15 07:21:00",
        }
        # 요청 전 상태, 1차 갱신 실패 후 상태, 재시도 요청 전 상태,
        # 2차 갱신 완료 후 상태
        status_sequence = [stale, stale, stale, fresh]

        with (
            patch.object(
                daily_refresh,
                "_dashboard_status",
                side_effect=status_sequence,
            ),
            patch.object(
                daily_refresh,
                "_request_dashboard_refresh",
                return_value={"status": "started"},
            ) as request_refresh,
            patch.object(daily_refresh.time, "sleep"),
        ):
            result = daily_refresh.run(now=now)

        self.assertEqual(result, 0)
        self.assertEqual(request_refresh.call_count, 2)


if __name__ == "__main__":
    unittest.main()
