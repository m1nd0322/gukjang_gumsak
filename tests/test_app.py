import unittest
from unittest.mock import patch

import app as app_module


class DeferredThread:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def start(self):
        return None


class FlaskApiTest(unittest.TestCase):
    def setUp(self):
        app_module.app.config.update(TESTING=False)
        self.client = app_module.app.test_client()
        with app_module.data_lock:
            app_module.current_data.update(
                status="idle",
                error_msg="",
                result=[],
                stats={},
                turn=[],
                supply=[],
                nps=[],
                last_updated=None,
            )
        with app_module.bt_lock:
            app_module.backtest_state.update(
                status="idle", results=None, error_msg="", progress="", engine=None
            )

    def test_refresh_reserves_loading_state_before_thread_starts(self):
        with patch.object(app_module.threading, "Thread", DeferredThread):
            first = self.client.post("/api/refresh")
            second = self.client.post("/api/refresh")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["status"], "started")
        self.assertEqual(second.get_json()["status"], "already_loading")

    def test_backtest_reserves_loading_state_before_thread_starts(self):
        with patch.object(app_module.threading, "Thread", DeferredThread):
            first = self.client.post("/api/backtest/run", json={})
            second = self.client.post("/api/backtest/run", json={})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["status"], "started")
        self.assertEqual(second.get_json()["status"], "already_loading")

    def test_backtest_rejects_invalid_numeric_and_strategy_inputs(self):
        invalid_number = self.client.post("/api/backtest/run", json={"period": "six"})
        invalid_strategy = self.client.post(
            "/api/backtest/run", json={"strategy": "unknown"}
        )
        with patch.object(app_module.threading, "Thread", DeferredThread):
            invalid_shape = self.client.post("/api/backtest/run", json=[])
            malformed_json = self.client.post(
                "/api/backtest/run", data="{", content_type="application/json"
            )

        self.assertEqual(invalid_number.status_code, 400)
        self.assertEqual(invalid_strategy.status_code, 400)
        self.assertEqual(invalid_shape.status_code, 400)
        self.assertEqual(malformed_json.status_code, 400)

    def test_db_routes_return_client_errors_for_invalid_requests(self):
        missing = self.client.get("/api/db/schema/not_a_table")
        bad_page_size = self.client.get("/api/db/query/daily_prices?page_size=0")
        bad_page = self.client.get("/api/db/query/daily_prices?page=nope")

        self.assertEqual(missing.status_code, 404)
        self.assertEqual(bad_page_size.status_code, 400)
        self.assertEqual(bad_page.status_code, 400)

    def test_db_viewer_uses_backend_stat_keys(self):
        template = app_module.DB_VIEWER_TEMPLATE

        self.assertIn("stats.total_records", template)
        self.assertIn("stats.total_tickers", template)
        self.assertIn("stats.date_min", template)
        self.assertIn("stats.date_max", template)


if __name__ == "__main__":
    unittest.main()
