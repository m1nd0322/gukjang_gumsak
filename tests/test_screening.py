import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
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
                "종목코드": "005930",
                "종목명": "삼성전자",
                "보통주": "458,637,667",
                "지분율(%)": "7.84",
                "최종변동일": "2022/08/16",
            },
        )

    def test_extracts_only_nps_share_change_rows(self):
        from screening import parse_nps_share_events

        html = """
        <html>
          <head><title>대웅제약(069620) | 지분분석 | FnGuide</title></head>
          <body><table><tbody id="sharebody">
            <tr>
              <td>국민연금공단</td><td>국민연금공단</td><td>본인</td>
              <td>2026/07/01</td><td>신규주요주주(+)</td><td>보통주</td>
              <td>0</td><td>+200</td><td>200</td><td>5.10</td>
            </tr>
            <tr>
              <td>국민연금공단</td><td>국민연금공단</td><td>본인</td>
              <td>2026.07.10</td><td>장내매도(-)</td><td>보통주</td>
              <td>200</td><td>-100</td><td>100</td><td>4.90</td>
            </tr>
            <tr>
              <td>KB자산운용</td><td>KB자산운용</td><td>본인</td>
              <td>2026/07/11</td><td>장내매수(+)</td><td>보통주</td>
              <td>100</td><td>+5</td><td>105</td><td>5.00</td>
            </tr>
            <tr>
              <td>국민연금공단</td><td>국민연금공단</td><td>본인</td>
              <td>2026/07/12</td><td>장내매수(+)</td><td>우선주</td>
              <td>100</td><td>+10</td><td>110</td><td>5.10</td>
            </tr>
          </tbody></table></body>
        </html>
        """

        rows = parse_nps_share_events(
            html, expected_code="069620", stock_name=" 대웅제약 "
        )

        self.assertEqual(
            rows,
            [
                {
                    "종목코드": "069620",
                    "종목명": "대웅제약",
                    "변동일": "2026-07-01",
                    "변동사유": "신규주요주주(+)",
                    "주식종류": "보통주",
                    "변동전": 0,
                    "증감": 200,
                    "변동후": 200,
                    "지분율(%)": 5.1,
                },
                {
                    "종목코드": "069620",
                    "종목명": "대웅제약",
                    "변동일": "2026-07-10",
                    "변동사유": "장내매도(-)",
                    "주식종류": "보통주",
                    "변동전": 200,
                    "증감": -100,
                    "변동후": 100,
                    "지분율(%)": 4.9,
                },
            ],
        )

    def test_share_events_reject_mismatched_ticker_page(self):
        from screening import parse_nps_share_events

        rows = parse_nps_share_events(
            self.html, expected_code="005935", stock_name="삼성전자우"
        )

        self.assertEqual(rows, [])

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


class NpsShareCollectorTest(unittest.TestCase):
    holdings = [
        {"종목코드": f"00000{index}", "종목명": chr(64 + index)}
        for index in range(1, 6)
    ]

    @staticmethod
    def _event(code, changed_at):
        return {
            "종목코드": code,
            "종목명": code,
            "변동일": changed_at,
            "변동사유": "장내매수(+)",
            "주식종류": "보통주",
            "변동전": 100,
            "증감": 10,
            "변동후": 110,
            "지분율(%)": 5.0,
        }

    def test_share_event_scan_sorts_rows_after_valid_coverage(self):
        from screening import fetch_nps_share_events

        def fetch_one(_name, code, **_kwargs):
            events = {
                "000001": [self._event("000001", "2026-07-02")],
                "000002": [
                    self._event("000002", "2026-07-03"),
                    self._event("000002", "2026-06-01"),
                ],
            }.get(code, [])
            return True, events

        with patch("screening._fetch_nps_share_one", side_effect=fetch_one):
            rows = fetch_nps_share_events(
                self.holdings[:2], require_coverage=True, max_workers=1
            )

        self.assertEqual(
            [(row["종목코드"], row["변동일"]) for row in rows],
            [
                ("000001", "2026-07-02"),
                ("000002", "2026-06-01"),
                ("000002", "2026-07-03"),
            ],
        )

    def test_share_event_scan_requires_eighty_percent_on_bootstrap(self):
        from screening import fetch_nps_share_events

        def fetch_one(_name, code, **_kwargs):
            return code in {"000001", "000002", "000003"}, [
                self._event(code, "2026-07-01")
            ]

        with patch("screening._fetch_nps_share_one", side_effect=fetch_one):
            with self.assertRaisesRegex(ScreeningDataError, "유효 페이지 비율"):
                fetch_nps_share_events(
                    self.holdings, require_coverage=True, max_workers=1
                )

    def test_share_event_scan_keeps_partial_rows_when_state_exists(self):
        from screening import fetch_nps_share_events

        def fetch_one(_name, code, **_kwargs):
            page_matches = code in {"000001", "000002", "000003"}
            events = [self._event(code, "2026-07-01")] if page_matches else []
            return page_matches, events

        with patch("screening._fetch_nps_share_one", side_effect=fetch_one):
            with self.assertLogs("screening", level="WARNING") as logs:
                rows = fetch_nps_share_events(
                    self.holdings, require_coverage=False, max_workers=1
                )

        self.assertEqual(len(rows), 3)
        self.assertIn("유효 페이지 비율이 낮습니다", "\n".join(logs.output))

    def test_share_event_scan_warns_on_request_failure(self):
        from screening import fetch_nps_share_events

        def fetch_one(_name, code, **_kwargs):
            if code == "000002":
                raise RuntimeError("network down")
            return True, [self._event(code, "2026-07-01")]

        with patch("screening._fetch_nps_share_one", side_effect=fetch_one):
            with self.assertLogs("screening", level="WARNING") as logs:
                rows = fetch_nps_share_events(
                    self.holdings[:2], require_coverage=False, max_workers=1
                )

        self.assertEqual([row["종목코드"] for row in rows], ["000001"])
        self.assertIn("조회 실패: 1/2", "\n".join(logs.output))


class NpsSignalBuilderTest(unittest.TestCase):
    def test_bootstrap_requires_full_share_analysis_coverage(self):
        from screening import build_nps_buy_signals

        holdings = [{"종목코드": "000001", "종목명": "A"}]
        candidate = {"version": 1, "holdings": {}, "signals": {}}
        with (
            patch("screening.load_nps_state", return_value=None),
            patch("screening.fetch_nps_holdings", return_value=holdings),
            patch("screening.fetch_nps_share_events", return_value=[]) as events,
            patch(
                "screening.reconcile_nps_signals",
                return_value=([{"종목명": "A"}], candidate),
            ) as reconcile,
        ):
            result = build_nps_buy_signals(
                "ticker_map.json",
                "nps_state.json",
                as_of=date(2026, 7, 12),
            )

        self.assertEqual(result, ([{"종목명": "A"}], candidate))
        events.assert_called_once_with(holdings, require_coverage=True)
        reconcile.assert_called_once_with(
            holdings, [], None, as_of=date(2026, 7, 12)
        )

    def test_existing_state_allows_partial_share_analysis_coverage(self):
        from screening import build_nps_buy_signals

        previous = {"version": 1, "holdings": {}, "signals": {}}
        holdings = [{"종목코드": "000001", "종목명": "A"}]
        with (
            patch("screening.load_nps_state", return_value=previous),
            patch("screening.fetch_nps_holdings", return_value=holdings),
            patch("screening.fetch_nps_share_events", return_value=[]) as events,
            patch(
                "screening.reconcile_nps_signals", return_value=([], previous)
            ),
        ):
            build_nps_buy_signals(
                "ticker_map.json",
                "nps_state.json",
                as_of=date(2026, 7, 12),
            )

        events.assert_called_once_with(holdings, require_coverage=False)


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
    candidate_state = {
        "version": 1,
        "updated_at": "2026-07-12",
        "holdings": {"000003": {"종목명": "C", "보통주": 1000}},
        "signals": {},
    }

    @patch(
        "screening.build_nps_buy_signals",
        return_value=([{"종목명": "C"}], candidate_state),
    )
    @patch("screening.fetch_supply_trend", return_value=[])
    @patch("screening.fetch_turnaround", side_effect=ScreeningDataError("broken"))
    def test_default_mode_preserves_successful_sources(
        self, _turnaround, _supply, _nps
    ):
        turn, supply, nps = fetch_all_data()

        self.assertEqual(turn, [])
        self.assertEqual(supply, [])
        self.assertEqual(nps, [{"종목명": "C"}])

    @patch(
        "screening.build_nps_buy_signals",
        return_value=([{"종목명": "C"}], candidate_state),
    )
    @patch("screening.fetch_supply_trend", return_value=[])
    @patch("screening.fetch_turnaround", side_effect=ScreeningDataError("broken"))
    def test_required_mode_rejects_a_failed_source(self, _turnaround, _supply, _nps):
        with self.assertRaises(ScreeningDataError):
            fetch_all_data(require_all=True)

    def test_complete_refresh_saves_candidate_nps_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "nps_state.json"
            with (
                patch("screening.fetch_turnaround", return_value=[{"종목명": "A"}]),
                patch("screening.fetch_supply_trend", return_value=[]),
                patch(
                    "screening.build_nps_buy_signals",
                    create=True,
                    return_value=([{"종목명": "C"}], self.candidate_state),
                ) as build_signals,
            ):
                turn, supply, nps = fetch_all_data(
                    "ticker_map.json",
                    require_all=True,
                    nps_state_path=state_path,
                    as_of=date(2026, 7, 12),
                )

            self.assertEqual(turn, [{"종목명": "A"}])
            self.assertEqual(supply, [])
            self.assertEqual(nps, [{"종목명": "C"}])
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8")),
                self.candidate_state,
            )
            build_signals.assert_called_once_with(
                "ticker_map.json",
                state_path,
                as_of=date(2026, 7, 12),
            )

    def test_failed_required_refresh_preserves_existing_nps_state_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "nps_state.json"
            original = b"trusted-state"
            state_path.write_bytes(original)
            with (
                patch(
                    "screening.fetch_turnaround",
                    side_effect=ScreeningDataError("broken"),
                ),
                patch("screening.fetch_supply_trend", return_value=[]),
                patch(
                    "screening.build_nps_buy_signals",
                    create=True,
                    return_value=([{"종목명": "C"}], self.candidate_state),
                ),
            ):
                with self.assertRaises(ScreeningDataError):
                    fetch_all_data(
                        "ticker_map.json",
                        require_all=True,
                        nps_state_path=state_path,
                        as_of=date(2026, 7, 12),
                    )

            self.assertEqual(state_path.read_bytes(), original)

    def test_partial_default_refresh_does_not_publish_candidate_state(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "nps_state.json"
            original = b"trusted-state"
            state_path.write_bytes(original)
            with (
                patch(
                    "screening.fetch_turnaround",
                    side_effect=ScreeningDataError("broken"),
                ),
                patch("screening.fetch_supply_trend", return_value=[]),
                patch(
                    "screening.build_nps_buy_signals",
                    return_value=([{"종목명": "C"}], self.candidate_state),
                ),
            ):
                turn, supply, nps = fetch_all_data(
                    "ticker_map.json",
                    nps_state_path=state_path,
                    as_of=date(2026, 7, 12),
                )

            self.assertEqual((turn, supply, nps), ([], [], [{"종목명": "C"}]))
            self.assertEqual(state_path.read_bytes(), original)

    def test_state_save_failure_is_reported_as_screening_error(self):
        with (
            patch("screening.fetch_turnaround", return_value=[]),
            patch("screening.fetch_supply_trend", return_value=[]),
            patch(
                "screening.build_nps_buy_signals",
                return_value=([], self.candidate_state),
            ),
            patch("screening.save_nps_state", side_effect=OSError("disk full")),
        ):
            with self.assertRaisesRegex(ScreeningDataError, "상태 저장 실패"):
                fetch_all_data(require_all=True)


if __name__ == "__main__":
    unittest.main()
