import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from itertools import combinations
from pathlib import Path
from unittest.mock import patch

import app as app_module


class DeferredThread:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    def start(self):
        return None


class BacktestFilterTest(unittest.TestCase):
    @staticmethod
    def all_subsets(values):
        return [
            subset
            for size in range(len(values) + 1)
            for subset in combinations(values, size)
        ]

    def test_all_64_score_and_item_filter_combinations(self):
        item_keys = tuple(app_module.BACKTEST_ITEM_SOURCES)
        item_sources = tuple(app_module.BACKTEST_ITEM_SOURCES.values())
        rows = []
        for index, sources in enumerate(self.all_subsets(item_sources)[1:], start=1):
            rows.append(
                {
                    "종목명": f"종목{index}",
                    "종합점수": len(sources),
                    "출처": ", ".join(sources),
                }
            )

        case_count = 0
        for selected_scores in self.all_subsets(app_module.BACKTEST_SCORE_OPTIONS):
            for required_items in self.all_subsets(item_keys):
                effective_scores = set(
                    selected_scores or app_module.BACKTEST_SCORE_OPTIONS
                )
                required_sources = {
                    app_module.BACKTEST_ITEM_SOURCES[key] for key in required_items
                }
                expected = [
                    row
                    for row in rows
                    if row["종합점수"] in effective_scores
                    and required_sources.issubset(
                        {source.strip() for source in row["출처"].split(",")}
                    )
                ]

                with self.subTest(
                    selected_scores=selected_scores,
                    required_items=required_items,
                ):
                    self.assertEqual(
                        app_module.filter_backtest_candidates(
                            rows, selected_scores, required_items
                        ),
                        expected,
                    )
                case_count += 1

        self.assertEqual(case_count, 64)


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
        try:
            with patch.object(app_module.threading, "Thread", DeferredThread):
                first = self.client.post("/api/refresh")
                second = self.client.post("/api/refresh")
        finally:
            if app_module.refresh_lock.locked():
                app_module.refresh_lock.release()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.get_json()["status"], "started")
        self.assertEqual(second.get_json()["status"], "already_loading")

    def test_refresh_api_does_not_overwrite_active_scheduler_state(self):
        with app_module.data_lock:
            app_module.current_data.update(
                status="done",
                last_updated="2026-07-12 08:00:00",
            )

        self.assertTrue(app_module.refresh_lock.acquire(blocking=False))
        try:
            with patch.object(app_module.threading, "Thread") as thread:
                response = self.client.post("/api/refresh")
        finally:
            app_module.refresh_lock.release()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["status"], "already_loading")
        self.assertEqual(app_module.current_data["status"], "done")
        thread.assert_not_called()

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

    def test_legacy_cache_without_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "cache_data.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "turn": [],
                        "supply": [],
                        "nps": [{"종목명": "구형보유"}],
                        "result": [{"종목명": "구형보유", "종합점수": 1}],
                        "stats": {"nps_count": 1},
                        "last_updated": "2026-07-11 08:00:00",
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(app_module, "CACHE_FILE", str(cache_path)):
                loaded = app_module.load_cache()

        self.assertFalse(loaded)
        self.assertEqual(app_module.current_data["status"], "idle")

    def test_refresh_writes_current_cache_version(self):
        stats = {"score_3": 0, "score_2": 0, "score_1": 0}
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "cache_data.json"
            with (
                patch.object(app_module, "CACHE_FILE", str(cache_path)),
                patch.object(
                    app_module, "fetch_all_data", return_value=([], [], [])
                ),
                patch.object(
                    app_module, "calculate_scores", return_value=([], stats)
                ),
                patch.object(
                    app_module.stock_db,
                    "replace_screening_results",
                    return_value=0,
                ),
            ):
                app_module.refresh_data()

            cache = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertEqual(cache["version"], app_module.CACHE_VERSION)

    def test_refresh_persists_results_before_publishing_cache(self):
        result = [
            {"종목명": "A", "종합점수": 1, "출처": "연간실적호전"}
        ]
        stats = {"score_3": 0, "score_2": 0, "score_1": 1}
        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "cache_data.json"

            def persist(rows):
                self.assertEqual(rows, result)
                self.assertEqual(app_module.current_data["result"], [])
                self.assertFalse(cache_path.exists())
                return 1

            with (
                patch.object(app_module, "CACHE_FILE", str(cache_path)),
                patch.object(
                    app_module, "fetch_all_data", return_value=([], [], [])
                ),
                patch.object(
                    app_module,
                    "calculate_scores",
                    return_value=(result, stats),
                ),
                patch.object(
                    app_module.stock_db,
                    "replace_screening_results",
                    side_effect=persist,
                ) as save_results,
            ):
                refreshed = app_module.refresh_data()

            cache = json.loads(cache_path.read_text(encoding="utf-8"))

        self.assertTrue(refreshed)
        save_results.assert_called_once_with(result)
        self.assertEqual(app_module.current_data["result"], result)
        self.assertEqual(cache["result"], result)

    def test_refresh_keeps_previous_state_when_duckdb_write_fails(self):
        previous = [{"종목명": "기존", "종합점수": 1}]
        with app_module.data_lock:
            app_module.current_data.update(
                result=previous,
                last_updated="2026-07-12 08:00:00",
                status="done",
            )

        with tempfile.TemporaryDirectory() as directory:
            cache_path = Path(directory) / "cache_data.json"
            cache_path.write_text('{"result": ["기존"]}', encoding="utf-8")
            original_cache = cache_path.read_bytes()

            with (
                patch.object(app_module, "CACHE_FILE", str(cache_path)),
                patch.object(
                    app_module, "fetch_all_data", return_value=([], [], [])
                ),
                patch.object(
                    app_module,
                    "calculate_scores",
                    return_value=(
                        [{"종목명": "신규", "종합점수": 1}],
                        {"score_3": 0, "score_2": 0, "score_1": 1},
                    ),
                ),
                patch.object(
                    app_module.stock_db,
                    "replace_screening_results",
                    side_effect=RuntimeError("duckdb write failed"),
                ),
            ):
                refreshed = app_module.refresh_data()

            self.assertEqual(cache_path.read_bytes(), original_cache)

        self.assertFalse(refreshed)
        self.assertEqual(app_module.current_data["result"], previous)
        self.assertEqual(app_module.current_data["status"], "done")

    def test_refresh_skips_when_another_refresh_holds_the_lock(self):
        self.assertTrue(app_module.refresh_lock.acquire(blocking=False))
        try:
            with patch.object(app_module, "fetch_all_data") as fetch:
                started = app_module.refresh_data()
        finally:
            app_module.refresh_lock.release()

        self.assertFalse(started)
        fetch.assert_not_called()

    def test_daily_refresh_runs_after_scheduler_wakes_up_late(self):
        refreshed = threading.Event()
        scheduler = app_module.scheduler
        scheduler.start(paused=True)
        try:
            scheduler.modify_job(
                "daily_refresh",
                func=refreshed.set,
            )
            scheduler.reschedule_job(
                "daily_refresh",
                trigger="date",
                run_date=datetime.now(scheduler.timezone) - timedelta(seconds=2),
            )
            scheduler.resume()

            self.assertTrue(
                refreshed.wait(timeout=2),
                "08:00을 놓친 뒤 깨어나도 당일 자동 갱신이 실행되어야 합니다",
            )
            deadline = time.monotonic() + 2
            while scheduler.get_job("daily_refresh") is not None:
                if time.monotonic() >= deadline:
                    self.fail("실행이 끝난 일회성 검증 잡이 제거되지 않았습니다")
                time.sleep(0.01)
        finally:
            scheduler.shutdown(wait=False)

    def test_dashboard_describes_time_bounded_nps_signal(self):
        self.assertIn("국민연금 신규/추가매수", app_module.HTML_TEMPLATE)
        self.assertIn("매수일부터 3개월 동안만 1점", app_module.HTML_TEMPLATE)
        self.assertIn("FnGuide 공개 주요주주 범위", app_module.HTML_TEMPLATE)

    def test_dashboard_escapes_screening_values_before_html_rendering(self):
        template = app_module.HTML_TEMPLATE

        self.assertIn("function escapeHtml(value)", template)
        self.assertIn("escapeHtml(r['종목명'])", template)
        self.assertIn("escapeHtml(v)", template)
        self.assertIn("escapeHtml(r[c]", template)


if __name__ == "__main__":
    unittest.main()
