import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from scripts.backup_stock_db import (
    backup_prefix,
    create_backup,
)
from stock_db import StockDB


class BackupScriptTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.db_path = self.root / "stock_data.duckdb"
        self.backup_dir = self.root / "backups"
        self.db = StockDB(str(self.db_path))
        self.db.save_prices(
            "005930",
            [
                {
                    "date": "2026-08-20",
                    "open": 100.0,
                    "high": 110.0,
                    "low": 99.0,
                    "close": 105.0,
                    "volume": 1_000,
                }
            ],
        )

    def test_backup_prefix_uses_the_korea_local_date(self):
        moment = datetime(2026, 8, 25, 7, 30)  # noqa: DTZ001

        self.assertEqual(backup_prefix(moment), "stock_data_20260825.duckdb")

    def test_creates_verified_backup_with_the_same_contents(self):
        import duckdb

        path, result = create_backup(self.db_path, self.backup_dir)

        self.assertEqual(result, "created")
        self.assertEqual(path.name, backup_prefix())
        source = duckdb.connect(str(self.db_path), read_only=True)
        copy = duckdb.connect(str(path), read_only=True)
        try:
            for table in ("daily_prices", "ticker_map", "index_prices"):
                source_count = source.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                copy_count = copy.execute(
                    f"SELECT COUNT(*) FROM {table}"
                ).fetchone()[0]
                with self.subTest(table=table):
                    self.assertEqual(source_count, copy_count)
        finally:
            source.close()
            copy.close()

    def test_second_run_on_the_same_day_is_skipped(self):
        first, first_result = create_backup(self.db_path, self.backup_dir)
        second, second_result = create_backup(self.db_path, self.backup_dir)

        self.assertEqual(first_result, "created")
        self.assertEqual(second_result, "exists")
        self.assertEqual(first, second)

    def test_missing_database_is_reported(self):
        path, result = create_backup(
            self.root / "없는파일.duckdb",
            self.backup_dir,
            attempts=1,
        )

        self.assertIsNone(path)
        self.assertEqual(result, "missing")
        self.assertFalse(self.backup_dir.exists())

    def test_keeps_only_the_newest_backups(self):
        stale_names = [
            "stock_data_20260801.duckdb",
            "stock_data_20260802.duckdb",
            "stock_data_20260803.duckdb",
        ]
        for name in stale_names:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            (self.backup_dir / name).write_bytes(b"old backup")

        create_backup(self.db_path, self.backup_dir, keep=2)

        remaining = sorted(
            path.name for path in self.backup_dir.glob("stock_data_*.duckdb")
        )
        self.assertEqual(remaining, ["stock_data_20260803.duckdb", backup_prefix()])

    def test_failed_verification_discards_the_temporary_copy_and_retries(self):
        with (
            patch(
                "scripts.backup_stock_db._verify_backup",
                side_effect=[RuntimeError("깨진 사본"), None],
            ) as verify,
            patch("scripts.backup_stock_db.time.sleep") as sleep,
        ):
            path, result = create_backup(
                self.db_path,
                self.backup_dir,
                attempts=2,
                retry_delay=0,
            )

        self.assertEqual(verify.call_count, 2)
        sleep.assert_called_once()
        self.assertEqual(result, "created")
        self.assertTrue(path.exists())
        leftovers = [
            name for name in os.listdir(self.backup_dir)
            if name.startswith(".")
        ]
        self.assertEqual(leftovers, [])

    def test_all_attempts_failing_reports_failure_without_leaving_files(self):
        with patch(
            "scripts.backup_stock_db._verify_backup",
            side_effect=RuntimeError("계속 깨짐"),
        ), patch("scripts.backup_stock_db.time.sleep"):
            path, result = create_backup(
                self.db_path,
                self.backup_dir,
                attempts=2,
                retry_delay=0,
            )

        self.assertIsNone(path)
        self.assertEqual(result, "failed")
        self.assertEqual(
            [name for name in os.listdir(self.backup_dir)],
            [],
            "실패한 시도의 파일이 남아 있으면 안 됩니다",
        )


if __name__ == "__main__":
    unittest.main()
