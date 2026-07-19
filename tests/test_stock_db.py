import os
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest.mock import patch

import duckdb
import pandas as pd

from stock_db import StockDB


class NeverCalledKrx:
    def __init__(self):
        self.ticker_calls = 0
        self.index_calls = 0

    def get_market_ticker_list(self, *args, **kwargs):
        self.ticker_calls += 1
        return []

    def get_index_ohlcv_by_date(self, *args, **kwargs):
        self.index_calls += 1
        raise AssertionError("완전한 캐시 범위에서는 호출되면 안 됩니다")


class StockDbCacheTest(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False)
        self.db_path = handle.name
        handle.close()
        os.unlink(self.db_path)
        self.db = StockDB(self.db_path)

    def tearDown(self):
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)

    def test_new_daily_prices_schema_includes_nullable_name(self):
        connection = self.db._connect()
        try:
            columns = connection.execute(
                "PRAGMA table_info('daily_prices')"
            ).fetchall()
        finally:
            connection.close()

        column_names = [row[1] for row in columns]
        self.assertIn("name", column_names)
        name_column = columns[column_names.index("name")]
        self.assertEqual(column_names[-1], "name")
        self.assertEqual(name_column[2], "VARCHAR")
        self.assertFalse(name_column[3])

    def test_legacy_daily_prices_schema_is_migrated_and_backfilled(self):
        handle = tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False)
        legacy_path = handle.name
        handle.close()
        os.unlink(legacy_path)

        try:
            connection = duckdb.connect(legacy_path)
            try:
                connection.execute("""
                    CREATE TABLE daily_prices (
                        ticker VARCHAR NOT NULL,
                        date DATE NOT NULL,
                        open DOUBLE,
                        high DOUBLE,
                        low DOUBLE,
                        close DOUBLE,
                        volume BIGINT,
                        PRIMARY KEY (ticker, date)
                    )
                """)
                connection.execute("""
                    CREATE TABLE ticker_map (
                        ticker VARCHAR PRIMARY KEY,
                        name VARCHAR NOT NULL,
                        market VARCHAR,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
                connection.execute(
                    "INSERT INTO ticker_map (ticker, name) VALUES (?, ?)",
                    ["005930", "삼성전자"],
                )
                connection.execute("""
                    INSERT INTO daily_prices
                        (ticker, date, open, high, low, close, volume)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, ["005930", "2026-01-05", 70000, 71000, 69500, 70500, 1000])
            finally:
                connection.close()

            StockDB(legacy_path)

            connection = duckdb.connect(legacy_path)
            try:
                columns = connection.execute(
                    "PRAGMA table_info('daily_prices')"
                ).fetchall()
                column_names = [row[1] for row in columns]
                row = connection.execute(
                    "SELECT * FROM daily_prices"
                ).fetchone()
            finally:
                connection.close()

            self.assertIn("name", column_names)
            self.assertEqual(column_names[-1], "name")
            self.assertEqual(row[column_names.index("name")], "삼성전자")
        finally:
            if os.path.exists(legacy_path):
                os.unlink(legacy_path)

    def test_save_prices_persists_mapped_name(self):
        connection = self.db._connect()
        try:
            connection.execute(
                "INSERT INTO ticker_map (ticker, name) VALUES (?, ?)",
                ["005930", "삼성전자"],
            )
        finally:
            connection.close()

        self.db.save_prices("005930", [{
            "date": "2026-01-05",
            "open": 70000,
            "high": 71000,
            "low": 69500,
            "close": 70500,
            "volume": 1000,
        }])

        connection = self.db._connect()
        try:
            name = connection.execute(
                "SELECT name FROM daily_prices "
                "WHERE ticker = ? AND date = ?",
                ["005930", "2026-01-05"],
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(name, "삼성전자")

    def test_save_prices_does_not_erase_existing_name_without_mapping(self):
        connection = self.db._connect()
        try:
            connection.execute(
                "INSERT INTO ticker_map (ticker, name) VALUES (?, ?)",
                ["005930", "삼성전자"],
            )
        finally:
            connection.close()

        self.db.save_prices("005930", [{
            "date": "2026-01-05",
            "open": 70000,
            "high": 71000,
            "low": 69500,
            "close": 70500,
            "volume": 1000,
        }])

        connection = self.db._connect()
        try:
            connection.execute(
                "DELETE FROM ticker_map WHERE ticker = ?", ["005930"]
            )
        finally:
            connection.close()

        self.db.save_prices("005930", [{
            "date": "2026-01-05",
            "open": 70500,
            "high": 71500,
            "low": 70000,
            "close": 71200,
            "volume": 1200,
        }])

        connection = self.db._connect()
        try:
            row = connection.execute(
                "SELECT name, close FROM daily_prices "
                "WHERE ticker = ? AND date = ?",
                ["005930", "2026-01-05"],
            ).fetchone()
        finally:
            connection.close()

        self.assertEqual(row, ("삼성전자", 71200.0))

    def test_save_prices_stores_null_name_for_unmapped_ticker(self):
        self.db.save_prices("999999", [{
            "date": "2026-01-05",
            "open": 1000,
            "high": 1100,
            "low": 900,
            "close": 1050,
            "volume": 100,
        }])

        page = self.db.query_table(
            "daily_prices", filter_col="ticker", filter_val="999999"
        )

        self.assertEqual(page["total"], 1)
        self.assertIsNone(page["rows"][0]["name"])

    def test_daily_prices_viewer_filters_by_stored_name(self):
        connection = self.db._connect()
        try:
            connection.execute(
                "INSERT INTO ticker_map (ticker, name) VALUES (?, ?)",
                ["005930", "삼성전자"],
            )
        finally:
            connection.close()
        self.db.save_prices("005930", [{
            "date": "2026-01-05",
            "open": 70000,
            "high": 71000,
            "low": 69500,
            "close": 70500,
            "volume": 1000,
        }])

        page = self.db.query_table(
            "daily_prices", filter_col="name", filter_val="삼성"
        )
        prices = self.db.get_prices(
            "005930", "2026-01-05", "2026-01-05"
        )

        self.assertEqual(page["total"], 1)
        self.assertEqual(page["rows"][0]["name"], "삼성전자")
        self.assertEqual(
            set(prices[0]),
            {"date", "open", "high", "low", "close", "volume"},
        )

    def test_ticker_cache_uses_latest_successful_refresh_time(self):
        old = (datetime.now() - timedelta(days=30)).isoformat()
        recent = datetime.now().isoformat()
        connection = self.db._connect()
        try:
            connection.executemany(
                "INSERT INTO ticker_map (ticker, name, market, updated_at) VALUES (?, ?, ?, ?)",
                [
                    ("000001", "오래된종목", "KOSPI", old),
                    ("000002", "최신종목", "KOSPI", recent),
                ],
            )
        finally:
            connection.close()

        krx = NeverCalledKrx()
        name_to_code, _ = self.db.get_or_refresh_ticker_map(krx)

        self.assertEqual(krx.ticker_calls, 0)
        self.assertEqual(name_to_code["최신종목"], "000002")

    def test_failed_ticker_refresh_keeps_stale_cache(self):
        old = (datetime.now() - timedelta(days=30)).isoformat()
        connection = self.db._connect()
        try:
            connection.execute(
                "INSERT INTO ticker_map (ticker, name, market, updated_at) "
                "VALUES (?, ?, ?, ?)",
                ["000001", "기존종목", "KOSPI", old],
            )
        finally:
            connection.close()

        name_to_code, _ = self.db.get_or_refresh_ticker_map(NeverCalledKrx())

        self.assertEqual(name_to_code, {"기존종목": "000001"})

    def test_empty_ticker_cache_bootstraps_from_repository_json(self):
        handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
        try:
            json.dump({"삼성전자": "005930"}, handle, ensure_ascii=False)
            handle.close()

            name_to_code, code_to_name = self.db.get_or_refresh_ticker_map(
                fallback_path=handle.name
            )

            self.assertEqual(name_to_code, {"삼성전자": "005930"})
            self.assertEqual(code_to_name, {"005930": "삼성전자"})
            self.assertEqual(
                self.db.get_ticker_map_from_db()[0], {"삼성전자": "005930"}
            )
        finally:
            if not handle.closed:
                handle.close()
            os.unlink(handle.name)

    @patch("stock_db.yf.download")
    def test_stock_prices_fall_back_to_yfinance_without_krx_login(self, download):
        download.return_value = pd.DataFrame(
            {
                "Open": [70_000.0],
                "High": [71_000.0],
                "Low": [69_500.0],
                "Close": [70_500.0],
                "Volume": [1_000_000],
            },
            index=pd.to_datetime(["2026-01-05"]),
        )

        added = self.db.fetch_and_store("005930", "20260105", "20260105")

        self.assertEqual(added, 1)
        self.assertEqual(
            self.db.get_prices("005930", "2026-01-05", "2026-01-05")[0]["close"],
            70_500.0,
        )
        self.assertEqual(download.call_args.args[0], "005930.KS")

    @patch("stock_db.yf.download")
    def test_index_prices_fall_back_to_yfinance_without_krx_login(self, download):
        download.return_value = pd.DataFrame(
            {"Close": [3_120.0]},
            index=pd.to_datetime(["2026-01-05"]),
        )

        added = self.db.ensure_index_data("1001", "20260105", "20260105")

        self.assertEqual(added, 1)
        self.assertEqual(
            self.db.get_index_prices("1001", "2026-01-05", "2026-01-05")[0]["close"],
            3_120.0,
        )
        self.assertEqual(download.call_args.args[0], "^KS11")

    def test_index_cache_skips_api_when_requested_range_is_present(self):
        self.db.save_index_prices(
            "1001",
            [
                {"date": "2026-01-02", "close": 3_100},
                {"date": "2026-01-05", "close": 3_120},
            ],
        )
        krx = NeverCalledKrx()

        added = self.db.ensure_index_data("1001", "20260102", "20260105", krx)

        self.assertEqual(added, 0)
        self.assertEqual(krx.index_calls, 0)

    def test_screening_results_store_requested_fields_and_are_queryable(self):
        saved = self.db.replace_screening_results(
            [
                {
                    "종목명": "삼성전자",
                    "종합점수": 2,
                    "출처": "연간실적호전, 순매수전환",
                    "순위": 1,
                    "[턴]PER": "12.3",
                    "[수급]수익률(%)": "4.2",
                }
            ],
            snapshot_date=date(2026, 7, 13),
        )

        page = self.db.query_table("screening_results", order_by="stock_name")

        self.assertEqual(saved, 1)
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["rows"][0]["stock_name"], "삼성전자")
        self.assertEqual(page["rows"][0]["score"], 2)
        self.assertEqual(
            page["rows"][0]["matched_items"],
            "연간실적호전, 순매수전환",
        )
        self.assertEqual(
            json.loads(page["rows"][0]["details"]),
            {"[턴]PER": "12.3", "[수급]수익률(%)": "4.2"},
        )

    def test_screening_results_replace_same_day_and_preserve_previous_days(self):
        self.db.replace_screening_results(
            [
                {
                    "종목명": "전날종목",
                    "종합점수": 1,
                    "출처": "연간실적호전",
                }
            ],
            snapshot_date=date(2026, 7, 12),
        )
        self.db.replace_screening_results(
            [
                {
                    "종목명": "유지종목",
                    "종합점수": 1,
                    "출처": "연간실적호전",
                },
                {
                    "종목명": "탈락종목",
                    "종합점수": 2,
                    "출처": "연간실적호전, 순매수전환",
                },
            ],
            snapshot_date=date(2026, 7, 13),
        )
        self.db.replace_screening_results(
            [
                {
                    "종목명": "유지종목",
                    "종합점수": 3,
                    "출처": (
                        "연간실적호전, 순매수전환, "
                        "국민연금 신규/추가매수"
                    ),
                }
            ],
            snapshot_date=date(2026, 7, 13),
        )

        connection = self.db._connect()
        try:
            rows = connection.execute(
                "SELECT CAST(snapshot_date AS VARCHAR), stock_name, score "
                "FROM screening_results ORDER BY snapshot_date, stock_name"
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(
            rows,
            [
                ("2026-07-12", "전날종목", 1),
                ("2026-07-13", "유지종목", 3),
            ],
        )

    def test_empty_screening_results_clear_only_requested_day(self):
        self.db.replace_screening_results(
            [{"종목명": "A", "종합점수": 1, "출처": "연간실적호전"}],
            snapshot_date=date(2026, 7, 12),
        )
        self.db.replace_screening_results(
            [{"종목명": "B", "종합점수": 1, "출처": "순매수전환"}],
            snapshot_date=date(2026, 7, 13),
        )

        saved = self.db.replace_screening_results(
            [], snapshot_date=date(2026, 7, 13)
        )

        connection = self.db._connect()
        try:
            rows = connection.execute(
                "SELECT CAST(snapshot_date AS VARCHAR), stock_name "
                "FROM screening_results"
            ).fetchall()
        finally:
            connection.close()

        self.assertEqual(saved, 0)
        self.assertEqual(rows, [("2026-07-12", "A")])

    def test_invalid_screening_result_preserves_existing_snapshot(self):
        existing = [
            {"종목명": "기존종목", "종합점수": 1, "출처": "연간실적호전"}
        ]
        self.db.replace_screening_results(
            existing, snapshot_date=date(2026, 7, 13)
        )

        with self.assertRaisesRegex(ValueError, "종목명"):
            self.db.replace_screening_results(
                [{"종합점수": 2, "출처": "연간실적호전, 순매수전환"}],
                snapshot_date=date(2026, 7, 13),
            )

        page = self.db.query_table("screening_results")
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["rows"][0]["stock_name"], "기존종목")

    @patch("stock_db.datetime")
    def test_default_screening_snapshot_date_uses_korea_timezone(self, clock):
        clock.now.return_value = datetime(2026, 7, 13, 8, 0)

        self.db.replace_screening_results(
            [{"종목명": "A", "종합점수": 1, "출처": "연간실적호전"}]
        )

        timezone = clock.now.call_args.args[0]
        self.assertEqual(str(timezone), "Asia/Seoul")
        page = self.db.query_table("screening_results")
        self.assertEqual(str(page["rows"][0]["snapshot_date"]), "2026-07-13")


if __name__ == "__main__":
    unittest.main()
