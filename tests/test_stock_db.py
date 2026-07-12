import os
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
