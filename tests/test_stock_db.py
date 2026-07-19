import os
import json
import tempfile
import threading
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


class NamedKrx:
    def __init__(self, name):
        self.name = name

    def get_market_ticker_list(self, _date, market):
        return ["005930"] if market == "KOSPI" else []

    def get_market_ticker_name(self, ticker):
        if ticker != "005930":
            raise AssertionError(f"예상하지 못한 티커: {ticker}")
        return self.name


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

    def test_memory_database_path_is_rejected_without_creating_file(self):
        original_directory = os.getcwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                with self.assertRaisesRegex(ValueError, "filesystem path"):
                    StockDB(":memory:")
                self.assertFalse(os.path.exists(":memory:"))
            finally:
                os.chdir(original_directory)

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

    def _assert_ticker_map_load_wins_over_inflight_price_save(
        self, price_db, mapping_db
    ):
        connection = price_db._connect()
        try:
            connection.execute(
                "INSERT INTO ticker_map (ticker, name) VALUES (?, ?)",
                ["005930", "기존이름"],
            )
        finally:
            connection.close()

        selected_old_name = threading.Event()
        release_price_save = threading.Event()
        map_load_started = threading.Event()
        allow_map_load = threading.Event()
        map_lock_attempted = threading.Event()
        map_load_finished = threading.Event()
        thread_errors = []
        original_connect = price_db._connect
        price_lock = getattr(price_db, "_mutation_lock", None)
        mapping_lock = getattr(mapping_db, "_mutation_lock", None)
        has_shared_lock = (
            price_lock is not None and price_lock is mapping_lock
        )

        class PausingConnection:
            def __init__(self, connection):
                self._connection = connection

            def execute(self, sql, parameters=None):
                if parameters is None:
                    result = self._connection.execute(sql)
                else:
                    result = self._connection.execute(sql, parameters)
                if "SELECT name FROM ticker_map" in " ".join(sql.split()):
                    selected_old_name.set()
                    if not release_price_save.wait(5):
                        raise AssertionError("가격 저장 재개 신호를 받지 못했습니다")
                return result

            def __getattr__(self, name):
                return getattr(self._connection, name)

        class NotifyingLock:
            def __init__(self, lock):
                self._lock = lock

            def __enter__(self):
                map_lock_attempted.set()
                self._lock.acquire()
                return self

            def __exit__(self, exc_type, exc_value, traceback):
                self._lock.release()

        def save_prices():
            try:
                price_db.save_prices("005930", [{
                    "date": "2026-01-05",
                    "open": 70000,
                    "high": 71000,
                    "low": 69500,
                    "close": 70500,
                    "volume": 1000,
                }])
            except Exception as exc:
                thread_errors.append(exc)

        map_file = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False
        )
        try:
            json.dump({"변경이름": "005930"}, map_file, ensure_ascii=False)
            map_file.close()

            def load_ticker_map():
                try:
                    map_load_started.set()
                    if not allow_map_load.wait(5):
                        raise AssertionError("종목 매핑 적재 허용 신호가 없습니다")
                    mapping_db.load_ticker_map_file(map_file.name)
                except Exception as exc:
                    thread_errors.append(exc)
                finally:
                    map_load_finished.set()

            save_thread = threading.Thread(target=save_prices)
            load_thread = threading.Thread(target=load_ticker_map)
            if has_shared_lock:
                mapping_db._mutation_lock = NotifyingLock(mapping_lock)
            with patch.object(
                price_db,
                "_connect",
                side_effect=lambda: PausingConnection(original_connect()),
            ):
                save_thread.start()
                try:
                    self.assertTrue(
                        selected_old_name.wait(5),
                        "가격 저장이 기존 종목명을 읽지 못했습니다",
                    )
                    if has_shared_lock:
                        acquired = price_lock.acquire(False)
                        if acquired:
                            price_lock.release()
                        self.assertFalse(
                            acquired,
                            "save_prices가 공용 잠금을 보유하지 않습니다",
                        )
                    load_thread.start()
                    self.assertTrue(
                        map_load_started.wait(5),
                        "종목 매핑 적재가 시작되지 않았습니다",
                    )
                    allow_map_load.set()
                    if has_shared_lock:
                        self.assertTrue(
                            map_lock_attempted.wait(5),
                            "매핑 적재가 공용 잠금 획득을 시도하지 않았습니다",
                        )
                        self.assertFalse(
                            map_load_finished.is_set(),
                            "매핑 적재가 가격 저장 잠금에서 차단되지 않았습니다",
                        )
                    else:
                        self.assertTrue(
                            map_load_finished.wait(5),
                            "종목 매핑 적재가 완료되지 않았습니다",
                        )
                finally:
                    allow_map_load.set()
                    release_price_save.set()
                    save_thread.join(5)
                    if load_thread.ident is not None:
                        load_thread.join(5)
                    if has_shared_lock:
                        mapping_db._mutation_lock = mapping_lock

            self.assertFalse(save_thread.is_alive(), "가격 저장 스레드가 멈췄습니다")
            self.assertFalse(load_thread.is_alive(), "매핑 적재 스레드가 멈췄습니다")
            if thread_errors:
                raise thread_errors[0]

            connection = price_db._connect()
            try:
                names = connection.execute("""
                    SELECT dp.name, tm.name
                    FROM daily_prices AS dp
                    JOIN ticker_map AS tm ON dp.ticker = tm.ticker
                    WHERE dp.ticker = ? AND dp.date = ?
                """, ["005930", "2026-01-05"]).fetchone()
            finally:
                connection.close()

            self.assertEqual(names, ("변경이름", "변경이름"))
            self.assertIs(price_db._mutation_lock, mapping_db._mutation_lock)
        finally:
            if not map_file.closed:
                map_file.close()
            os.unlink(map_file.name)

    def test_ticker_map_load_wins_over_inflight_price_save(self):
        alias_path = os.path.join(
            os.path.dirname(self.db_path),
            ".",
            os.path.basename(self.db_path),
        )
        mapping_db = StockDB(alias_path)

        self._assert_ticker_map_load_wins_over_inflight_price_save(
            self.db, mapping_db
        )

    def test_case_aliases_share_mutation_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            upper_path = os.path.join(directory, "RaceCase.duckdb")
            lower_path = os.path.join(directory, "racecase.duckdb")
            price_db = StockDB(upper_path)
            if not os.path.exists(lower_path):
                self.skipTest("case-sensitive filesystem")
            self.assertTrue(os.path.samefile(upper_path, lower_path))
            mapping_db = StockDB(lower_path)

            self._assert_ticker_map_load_wins_over_inflight_price_save(
                price_db, mapping_db
            )

    def test_concurrent_case_alias_creation_shares_mutation_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            probe_path = os.path.join(directory, "CaseProbe")
            os.mkdir(probe_path)
            if not os.path.exists(os.path.join(directory, "caseprobe")):
                self.skipTest("case-sensitive filesystem")

            upper_path = os.path.join(directory, "RaceCase.duckdb")
            lower_path = os.path.join(directory, "racecase.duckdb")
            start_construction = threading.Event()
            instances = []
            thread_errors = []

            def create_database(path):
                try:
                    if not start_construction.wait(5):
                        raise AssertionError("DB 생성 시작 신호가 없습니다")
                    instances.append(StockDB(path))
                except Exception as exc:
                    thread_errors.append(exc)

            threads = [
                threading.Thread(target=create_database, args=(upper_path,)),
                threading.Thread(target=create_database, args=(lower_path,)),
            ]
            for thread in threads:
                thread.start()
            start_construction.set()
            for thread in threads:
                thread.join(5)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertFalse(
                thread_errors,
                f"동시 DB 생성이 실패했습니다: {thread_errors}",
            )
            self.assertEqual(len(instances), 2)
            self.assertTrue(os.path.samefile(upper_path, lower_path))
            self.assertIs(
                instances[0]._mutation_lock,
                instances[1]._mutation_lock,
            )

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

    def test_ticker_map_file_backfills_missing_daily_price_name(self):
        self.db.save_prices("005930", [{
            "date": "2026-01-05",
            "open": 70000,
            "high": 71000,
            "low": 69500,
            "close": 70500,
            "volume": 1000,
        }])
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False
        )
        try:
            json.dump({"삼성전자": "005930"}, handle, ensure_ascii=False)
            handle.close()
            self.db.load_ticker_map_file(handle.name)

            page = self.db.query_table(
                "daily_prices", filter_col="ticker", filter_val="005930"
            )
            self.assertEqual(page["rows"][0]["name"], "삼성전자")
        finally:
            if not handle.closed:
                handle.close()
            os.unlink(handle.name)

    def test_ticker_map_file_renames_daily_price_names(self):
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
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False
        )
        try:
            json.dump(
                {"삼성전자우선": "005930"}, handle, ensure_ascii=False
            )
            handle.close()
            self.db.load_ticker_map_file(handle.name)

            page = self.db.query_table(
                "daily_prices", filter_col="ticker", filter_val="005930"
            )
            self.assertEqual(page["rows"][0]["name"], "삼성전자우선")
        finally:
            if not handle.closed:
                handle.close()
            os.unlink(handle.name)

    def test_refresh_ticker_map_updates_existing_daily_price_name(self):
        connection = self.db._connect()
        try:
            connection.execute(
                "INSERT INTO ticker_map (ticker, name) VALUES (?, ?)",
                ["005930", "기존이름"],
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

        self.db.refresh_ticker_map(NamedKrx("변경이름"))

        connection = self.db._connect()
        try:
            row = connection.execute("""
                SELECT dp.name, tm.name
                FROM daily_prices dp
                JOIN ticker_map tm ON dp.ticker = tm.ticker
                WHERE dp.ticker = ?
            """, ["005930"]).fetchone()
        finally:
            connection.close()
        self.assertEqual(row, ("변경이름", "변경이름"))

    def test_ticker_map_load_rolls_back_when_name_sync_fails(self):
        connection = self.db._connect()
        try:
            connection.execute(
                "INSERT INTO ticker_map (ticker, name) VALUES (?, ?)",
                ["005930", "기존이름"],
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

        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False
        )
        try:
            json.dump({"변경이름": "005930"}, handle, ensure_ascii=False)
            handle.close()
            with patch.object(
                self.db,
                "_sync_daily_price_names",
                side_effect=RuntimeError("name sync failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "name sync failed"):
                    self.db.load_ticker_map_file(handle.name)

            connection = self.db._connect()
            try:
                row = connection.execute("""
                    SELECT dp.name, tm.name
                    FROM daily_prices dp
                    JOIN ticker_map tm ON dp.ticker = tm.ticker
                    WHERE dp.ticker = ?
                """, ["005930"]).fetchone()
            finally:
                connection.close()
            self.assertEqual(row, ("기존이름", "기존이름"))
        finally:
            if not handle.closed:
                handle.close()
            os.unlink(handle.name)

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
