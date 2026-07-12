import json
import os
import tempfile
import unittest
from unittest.mock import patch

from screening import (
    ScreeningDataError,
    calculate_scores,
    fetch_all_data,
    fetch_nps_holdings,
    fetch_supply_trend,
    fetch_turnaround,
    parse_nps_holding,
)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.content = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, *args, **kwargs):
        return self.response


def bom_json(payload):
    return b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8")


class ScreeningFeedTest(unittest.TestCase):
    def test_turnaround_feed_decodes_bom_and_preserves_legacy_columns(self):
        payload = {
            "comp": [
                {
                    "RN": "1",
                    "GICODE": "A005930",
                    "ITEMABBRNM": " 삼성전자 ",
                    "CUR_GSYM": "2025/12",
                    "CUR_DATA": "10.0",
                    "PREV_DATA": "-1.0",
                    "GROWTH_NM": "흑자전환",
                    "PER": "12.3",
                    "PBR": "1.4",
                }
            ]
        }
        rows = fetch_turnaround(session=FakeSession(FakeResponse(bom_json(payload))))

        self.assertEqual(
            rows,
            [
                {
                    "No.": "1",
                    "종목명": "삼성전자",
                    "결산년월": "2025/12",
                    "최근결산 영업이익": "10.0",
                    "직전결산 영업이익": "-1.0",
                    "증가율": "흑자전환",
                    "PER": "12.3",
                    "PBR": "1.4",
                }
            ],
        )

    def test_supply_feed_maps_current_json_shape(self):
        payload = {
            "comp": [
                {
                    "RN": "1",
                    "ITEMABBRNM": "SK 하이닉스",
                    "CLS_PRC": "250,000",
                    "YIELD": "2.4",
                    "SUM_AMT": "150.5",
                }
            ]
        }
        rows = fetch_supply_trend(session=FakeSession(FakeResponse(bom_json(payload))))

        self.assertEqual(rows[0]["종목명"], "SK 하이닉스")
        self.assertEqual(rows[0]["전일종가(원)"], "250,000")
        self.assertEqual(rows[0]["순매수금액(억원)"], "150.5")

    def test_feed_rejects_http_200_error_document(self):
        session = FakeSession(FakeResponse(b"<html>404 - page not found</html>"))

        with self.assertRaises(ScreeningDataError):
            fetch_turnaround(session=session)


class NpsParserTest(unittest.TestCase):
    html = """
    <html>
      <head><title>삼성전자(005930) | Snapshot | FnGuide</title></head>
      <body>
        <table>
          <caption>주주현황</caption>
          <tbody>
            <tr>
              <th title="국민연금공단"><a>국민연금공단</a></th>
              <td>458,637,667</td><td>7.84</td><td>2022/08/16</td>
            </tr>
          </tbody>
        </table>
      </body>
    </html>
    """

    def test_extracts_nps_row_when_page_ticker_matches(self):
        row = parse_nps_holding(
            self.html, expected_code="005930", stock_name="삼성전자"
        )

        self.assertEqual(
            row,
            {
                "종목명": "삼성전자",
                "보통주": "458,637,667",
                "지분율(%)": "7.84",
                "최종변동일": "2022/08/16",
            },
        )

    def test_rejects_preferred_share_redirected_to_common_stock(self):
        row = parse_nps_holding(
            self.html, expected_code="005935", stock_name="삼성전자우"
        )

        self.assertIsNone(row)

    def test_full_scan_rejects_invalid_page_coverage(self):
        handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)
        try:
            json.dump({"A": "000001", "B": "000002"}, handle)
            handle.close()
            with patch(
                "screening._fetch_nps_one",
                side_effect=lambda _name, code, **_kwargs: (
                    code == "000001",
                    None,
                ),
            ):
                with self.assertRaises(ScreeningDataError):
                    fetch_nps_holdings(handle.name, max_workers=1)
        finally:
            if not handle.closed:
                handle.close()
            os.unlink(handle.name)


class ScoringTest(unittest.TestCase):
    def test_scores_and_details_remain_compatible(self):
        turn = [{"종목명": "A", "PER": "10"}, {"종목명": "B"}]
        supply = [{"종목명": "A", "수익률(%)": "2"}]
        nps = [{"종목명": "A", "지분율(%)": "7"}, {"종목명": "C"}]

        results, stats = calculate_scores(turn, supply, nps)

        self.assertEqual(results[0]["종목명"], "A")
        self.assertEqual(results[0]["종합점수"], 3)
        self.assertEqual(results[0]["순위"], 1)
        self.assertEqual(results[0]["[턴]PER"], "10")
        self.assertEqual(results[0]["[수급]수익률(%)"], "2")
        self.assertEqual(results[0]["[연금]지분율(%)"], "7")
        self.assertEqual(stats["score_3"], 1)
        self.assertEqual(stats["total"], 3)


class SourceOrchestrationTest(unittest.TestCase):
    @patch("screening.fetch_nps_holdings", return_value=[{"종목명": "C"}])
    @patch("screening.fetch_supply_trend", return_value=[])
    @patch("screening.fetch_turnaround", side_effect=ScreeningDataError("broken"))
    def test_default_mode_preserves_successful_sources(
        self, _turnaround, _supply, _nps
    ):
        turn, supply, nps = fetch_all_data()

        self.assertEqual(turn, [])
        self.assertEqual(supply, [])
        self.assertEqual(nps, [{"종목명": "C"}])

    @patch("screening.fetch_nps_holdings", return_value=[{"종목명": "C"}])
    @patch("screening.fetch_supply_trend", return_value=[])
    @patch("screening.fetch_turnaround", side_effect=ScreeningDataError("broken"))
    def test_required_mode_rejects_a_failed_source(self, _turnaround, _supply, _nps):
        with self.assertRaises(ScreeningDataError):
            fetch_all_data(require_all=True)


if __name__ == "__main__":
    unittest.main()
