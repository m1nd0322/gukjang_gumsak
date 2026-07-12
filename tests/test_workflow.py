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


if __name__ == "__main__":
    unittest.main()
