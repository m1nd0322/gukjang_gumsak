import unittest
from pathlib import Path


class WorkflowStateCacheTest(unittest.TestCase):
    def test_daily_report_restores_and_saves_nps_state(self):
        workflow = Path(".github/workflows/daily_report.yml").read_text(
            encoding="utf-8"
        )

        self.assertIn("actions/cache/restore@v4", workflow)
        self.assertIn("actions/cache/save@v4", workflow)
        self.assertGreaterEqual(workflow.count("nps_state.json"), 2)
        self.assertIn("nps-state-", workflow)
        self.assertLess(
            workflow.index("actions/cache/restore@v4"),
            workflow.index("run: python daily_report.py"),
        )
        self.assertLess(
            workflow.index("run: python daily_report.py"),
            workflow.index("actions/cache/save@v4"),
        )
        self.assertIn("if: success()", workflow)
        self.assertIn("concurrency:", workflow)
        self.assertIn("group: daily-report-nps-state", workflow)
        self.assertIn("cancel-in-progress: false", workflow)
        self.assertGreaterEqual(workflow.count("github.run_attempt"), 2)

    def test_daily_report_restores_and_saves_screening_duckdb(self):
        workflow = Path(".github/workflows/daily_report.yml").read_text(
            encoding="utf-8"
        )

        restore = workflow.index("DuckDB 종합결과 복원")
        report = workflow.index("run: python daily_report.py")
        save = workflow.index("DuckDB 종합결과 저장")

        self.assertGreaterEqual(workflow.count("stock_data.duckdb"), 2)
        self.assertGreaterEqual(workflow.count("screening-db-"), 3)
        self.assertLess(restore, report)
        self.assertLess(report, save)


class ReadmeRunbookTest(unittest.TestCase):
    def test_dashboard_runbook_uses_uv_managed_python_on_every_os(self):
        readme = Path("README.md").read_text(encoding="utf-8")
        command = (
            "uv run --isolated --managed-python --python 3.11 "
            "--with-requirements requirements.txt python app.py"
        )

        self.assertIn(command, readme)
        self.assertIn("macOS, Linux, Windows PowerShell/CMD에서 동일", readme)
        self.assertIn("http://localhost:5000", readme)
        self.assertIn("http://localhost:5000/backtest", readme)
        self.assertIn("http://localhost:5000/db", readme)


if __name__ == "__main__":
    unittest.main()
