import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from stock_screener import generate_html


class StaticReportTest(unittest.TestCase):
    def test_nps_buy_signal_category_and_expiry_are_rendered(self):
        result = pd.DataFrame(
            [
                {
                    "종목명": "A",
                    "종합점수": 1,
                    "출처": "국민연금 신규/추가매수",
                    "[연금]매수구분": "추가매수",
                    "[연금]매수일": "2026-06-30",
                    "[연금]만료일": "2026-09-30",
                }
            ],
            index=[1],
        )
        nps = pd.DataFrame(
            [
                {
                    "종목명": "A",
                    "매수구분": "추가매수",
                    "매수일": "2026-06-30",
                    "만료일": "2026-09-30",
                }
            ]
        )
        stats = {
            "turn_count": 0,
            "supply_count": 0,
            "nps_count": 1,
            "total": 1,
            "score_3": 0,
            "score_2": 0,
            "score_1": 1,
        }
        with TemporaryDirectory() as directory:
            output_path = Path(directory) / "report.html"

            generate_html(
                result,
                pd.DataFrame(),
                pd.DataFrame(),
                nps,
                stats,
                output_path,
            )

            html = output_path.read_text(encoding="utf-8")

        self.assertIn("국민연금 신규/추가매수", html)
        self.assertIn("국민연금 매수</span>", html)
        self.assertIn("만료일: 2026-09-30", html)
        self.assertIn("FnGuide 공개 주요주주 범위", html)


if __name__ == "__main__":
    unittest.main()
