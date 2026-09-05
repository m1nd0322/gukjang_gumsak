import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import ClassVar
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

    @property
    def text(self):
        return self.content.decode("utf-8")


class FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, *args, **kwargs):
        return self.response


class RoutingSession:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.handler(url, kwargs)


def bom_json(payload):
    return b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8")


class ScreeningFeedTest(unittest.TestCase):
    def test_turnaround_feed_maps_new_fnguide_response_to_legacy_columns(self):
        payload = {
            "comp": [],
            "dataset": {
                "data": [
                    {
                        "CMP_CD": "005930",
                        "CMP_KOR": " 삼성전자 ",
                        "P_SEC_KOR": "IT",
                        "SEC_KOR": "반도체",
                        "MKT_KOR": "유",
                        "FREQ": "Annual",
                        "CUR_VAL": 10.0,
                        "BEF_VAL": -1.0,
                        "GROWTH": "흑자전환",
                        "GROWTH_VAL": 1100.0,
                        "DATA_GUBUN": "확정",
                        "FIN_DT": "2026-03-19",
                    },
                    {
                        "CMP_CD": "000000",
                        "CMP_KOR": "0값테스트",
                        "FREQ": "Annual",
                        "CUR_VAL": 0.0,
                        "BEF_VAL": 0,
                        "GROWTH": "0.0%",
                    }
                ]
            },
        }
        session = RoutingSession(
            lambda _url, _kwargs: FakeResponse(bom_json(payload))
        )

        rows = fetch_turnaround(session=session)

        self.assertEqual(
            rows,
            [
                {
                    "No.": "1",
                    "종목명": "삼성전자",
                    "결산년월": "연간",
                    "최근결산 영업이익": "10.0",
                    "직전결산 영업이익": "-1.0",
                    "증가율": "흑자전환",
                    "PER": "",
                    "PBR": "",
                },
                {
                    "No.": "2",
                    "종목명": "0값테스트",
                    "결산년월": "연간",
                    "최근결산 영업이익": "0.0",
                    "직전결산 영업이익": "0",
                    "증가율": "0.0%",
                    "PER": "",
                    "PBR": "",
                },
            ],
        )
        self.assertEqual(
            session.calls[0][1]["params"],
            {"prc": 1, "consol_typ": "C", "fin_typ": "O", "freq_typ": "Y"},
        )

    def test_supply_feed_keeps_only_new_joint_net_buy_transitions(self):
        def rank_row(
            rank, name, code, price, change_rate, change_price, volume, amount
        ):
            return {
                "rank": rank,
                "name": name,
                "symbolCode": f"A{code}",
                "code": f"KR7{code}001",
                "tradePrice": price,
                "change": "RISE" if change_price >= 0 else "FALL",
                "changeRate": change_rate,
                "changePrice": change_price,
                "straightPurchaseVolume": volume,
                "straightPurchasePrice": amount,
            }

        def ranking_payload(rows):
            return {
                "data": {"BUY": rows, "SELL": []},
                "fromDate": "2026-08-05",
                "toDate": "2026-08-05",
            }

        rankings = {
            ("KOSPI", "FOREIGN"): ranking_payload(
                [
                    rank_row(1, "삼성전자", "005930", 100000, 0.012, 1200, 100000, 10000000000),
                    rank_row(2, "SK하이닉스", "000660", 200000, 0.01, 2000, 100000, 5000000000),
                    rank_row(3, "TIGER ETF", "133690", 50000, 0.005, 250, 80000, 4000000000),
                ]
            ),
            ("KOSPI", "INSTITUTION"): ranking_payload(
                [
                    rank_row(1, "삼성전자", "005930", 100000, 0.012, 1200, 50000, 5000000000),
                    rank_row(2, "LG전자", "066570", 90000, -0.01, -900, 10000, 900000000),
                ]
            ),
            ("KOSDAQ", "FOREIGN"): ranking_payload(
                [rank_row(1, "에코프로", "086520", 50000, -0.01, -500, 10000, 500000000)]
            ),
            ("KOSDAQ", "INSTITUTION"): ranking_payload([]),
        }

        def history_row(day, foreign, institution, price, change_price):
            return {
                "date": f"{day} 00:00:00",
                "foreignOwnShares": 1000000,
                "foreignOwnSharesRate": 0.1,
                "foreignStraightPurchaseVolume": foreign,
                "institutionStraightPurchaseVolume": institution,
                "institutionCumulativeStraightPurchaseVolume": institution,
                "tradePrice": price,
                "changePrice": change_price,
                "change": "RISE" if change_price >= 0 else "FALL",
                "accTradeVolume": 1000000,
                "accTradePrice": price * 1000000,
            }

        histories = {
            "A005930": [
                history_row("2026-08-05", 100000, 50000, 100000, 1200),
                history_row("2026-08-04", 100000, -10, 98800, 100),
            ],
            "A000660": [
                history_row("2026-08-05", 100000, 100000, 200000, 2000),
                history_row("2026-08-04", 10, 20, 198000, 1000),
            ],
            "A066570": [
                history_row("2026-08-05", -10, 10000, 90000, -900),
                history_row("2026-08-04", -20, -30, 90900, -100),
            ],
            "A086520": [
                history_row("2026-08-05", 10000, 10000, 50000, -500),
                history_row("2026-08-04", -100, 200, 50500, 500),
            ],
        }

        def route(url, kwargs):
            if "SUPPLY_TREND_FIRST_BUY" in url:
                return FakeResponse(bom_json({"comp": []}))
            if url.endswith("/api/trend/investor_purchase"):
                params = kwargs["params"]
                payload = rankings[(params["market"], params["investorType"])]
                return FakeResponse(bom_json(payload))
            if url.endswith("/api/investor/days"):
                symbol_code = kwargs["params"]["symbolCode"]
                payload = {
                    "code": 200,
                    "message": "OK",
                    "data": histories[symbol_code],
                    "totalPages": 1,
                    "totalCount": 2,
                    "currentPage": 1,
                    "pageSize": 2,
                }
                return FakeResponse(bom_json(payload))
            raise AssertionError(f"unexpected URL: {url}")

        rows = fetch_supply_trend(session=RoutingSession(route))

        self.assertEqual(
            rows,
            [
                {
                    "No.": "1",
                    "종목명": "삼성전자",
                    "전일종가(원)": "100,000",
                    "수익률(%)": "1.2",
                    "순매수금액(억원)": "150.0",
                },
                {
                    "No.": "2",
                    "종목명": "에코프로",
                    "전일종가(원)": "50,000",
                    "수익률(%)": "-1.0",
                    "순매수금액(억원)": "10.0",
                },
            ],
        )

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

    def test_snapshot_fetch_rejects_matching_title_without_shareholder_table(self):
        from screening import _fetch_nps_one

        html = """
        <html>
          <head><title>삼성전자(005930) | Snapshot | FnGuide</title></head>
          <body>changed shareholder markup</body>
        </html>
        """
        session = FakeSession(FakeResponse(html.encode("utf-8")))

        page_matches, row = _fetch_nps_one(
            "삼성전자",
            "005930",
            timeout=1,
            session_getter=lambda: session,
        )

        self.assertFalse(page_matches)
        self.assertIsNone(row)

    def test_snapshot_fetch_accepts_valid_table_without_nps_row(self):
        from screening import _fetch_nps_one

        html = """
        <html>
          <head><title>삼성전자(005930) | Snapshot | FnGuide</title></head>
          <body><table>
            <caption class="cphidden">주주현황</caption>
            <tbody><tr><th title="다른주주">다른주주</th></tr></tbody>
          </table></body>
        </html>
        """
        session = FakeSession(FakeResponse(html.encode("utf-8")))

        page_matches, row = _fetch_nps_one(
            "삼성전자",
            "005930",
            timeout=1,
            session_getter=lambda: session,
        )

        self.assertTrue(page_matches)
        self.assertIsNone(row)

    def test_snapshot_fetch_rejects_malformed_nps_row_in_valid_table(self):
        from screening import _fetch_nps_one

        html = """
        <html>
          <head><title>삼성전자(005930) | Snapshot | FnGuide</title></head>
          <body><table>
            <caption class="cphidden">주주현황</caption>
            <tbody><tr>
              <th title="국민연금공단">국민연금공단</th>
              <td>changed layout</td>
            </tr></tbody>
          </table></body>
        </html>
        """
        session = FakeSession(FakeResponse(html.encode("utf-8")))

        page_matches, row = _fetch_nps_one(
            "삼성전자",
            "005930",
            timeout=1,
            session_getter=lambda: session,
        )

        self.assertFalse(page_matches)
        self.assertIsNone(row)

    def test_share_fetch_rejects_matching_page_without_sharebody(self):
        from screening import _fetch_nps_share_one

        html = """
        <html>
          <head><title>삼성전자(005930) | 지분분석 | FnGuide</title></head>
          <body>changed markup</body>
        </html>
        """
        session = FakeSession(FakeResponse(html.encode("utf-8")))

        page_matches, rows = _fetch_nps_share_one(
            "삼성전자",
            "005930",
            timeout=1,
            session_getter=lambda: session,
        )

        self.assertFalse(page_matches)
        self.assertEqual(rows, [])

    def test_share_fetch_rejects_unstructured_sharebody(self):
        from screening import _fetch_nps_share_one

        html = """
        <html>
          <head><title>삼성전자(005930) | 지분분석 | FnGuide</title></head>
          <body><table id="tbl_own_chg">
            <caption class="cphidden">주주변동내역</caption>
            <tbody id="sharebody">
            <tr><td>changed layout</td></tr>
          </tbody></table></body>
        </html>
        """
        session = FakeSession(FakeResponse(html.encode("utf-8")))

        page_matches, rows = _fetch_nps_share_one(
            "삼성전자",
            "005930",
            timeout=1,
            session_getter=lambda: session,
        )

        self.assertFalse(page_matches)
        self.assertEqual(rows, [])

    def test_share_fetch_accepts_valid_empty_change_table(self):
        from screening import _fetch_nps_share_one

        html = """
        <html>
          <head><title>삼성전자(005930) | 지분분석 | FnGuide</title></head>
          <body><table id="tbl_own_chg">
            <caption class="cphidden">주주변동내역</caption>
            <tbody id="sharebody"></tbody>
          </table></body>
        </html>
        """
        session = FakeSession(FakeResponse(html.encode("utf-8")))

        page_matches, rows = _fetch_nps_share_one(
            "삼성전자",
            "005930",
            timeout=1,
            session_getter=lambda: session,
        )

        self.assertTrue(page_matches)
        self.assertEqual(rows, [])

    def test_full_scan_rejects_invalid_page_coverage(self):
        handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)  # noqa: SIM115
        try:
            json.dump({"A": "000001", "B": "000002"}, handle)
            handle.close()
            with patch(
                "screening._fetch_nps_one",
                side_effect=lambda _name, code, **_kwargs: (
                    code == "000001",
                    None,
                ),
            ), self.assertRaises(ScreeningDataError):
                fetch_nps_holdings(handle.name, max_workers=1)
        finally:
            if not handle.closed:
                handle.close()
            os.unlink(handle.name)

    def test_full_scan_rejects_failed_snapshot_for_previous_holding(self):
        handle = tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", delete=False)  # noqa: SIM115
        try:
            json.dump(
                {f"종목{index}": f"00000{index}" for index in range(1, 6)},
                handle,
            )
            handle.close()

            def fetch_one(name, code, **_kwargs):
                if code == "000005":
                    return False, None
                return True, {
                    "종목코드": code,
                    "종목명": name,
                    "보통주": "1,000",
                    "지분율(%)": "5.0",
                    "최종변동일": "2026/07/01",
                }

            with patch("screening._fetch_nps_one", side_effect=fetch_one), self.assertRaisesRegex(
                ScreeningDataError, "기존 보유 종목"
            ):
                fetch_nps_holdings(
                    handle.name,
                    max_workers=1,
                    required_codes={"000005"},
                )
        finally:
            if not handle.closed:
                handle.close()
            os.unlink(handle.name)


class NpsShareCollectorTest(unittest.TestCase):
    holdings: ClassVar[list[dict]] = [
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

        verified_codes = set()
        with patch("screening._fetch_nps_share_one", side_effect=fetch_one):
            rows = fetch_nps_share_events(
                self.holdings[:2],
                require_coverage=True,
                max_workers=1,
                verified_codes=verified_codes,
            )

        self.assertEqual(
            [(row["종목코드"], row["변동일"]) for row in rows],
            [
                ("000001", "2026-07-02"),
                ("000002", "2026-06-01"),
                ("000002", "2026-07-03"),
            ],
        )
        self.assertEqual(verified_codes, {"000001", "000002"})

    def test_share_event_scan_requires_eighty_percent_on_bootstrap(self):
        from screening import fetch_nps_share_events

        def fetch_one(_name, code, **_kwargs):
            return code in {"000001", "000002", "000003"}, [
                self._event(code, "2026-07-01")
            ]

        with patch("screening._fetch_nps_share_one", side_effect=fetch_one), self.assertRaisesRegex(
            ScreeningDataError, "유효 페이지 비율"
        ):
            fetch_nps_share_events(
                self.holdings, require_coverage=True, max_workers=1
            )

    def test_share_event_scan_keeps_partial_rows_when_state_exists(self):
        from screening import fetch_nps_share_events

        def fetch_one(_name, code, **_kwargs):
            page_matches = code in {"000001", "000002", "000003"}
            events = [self._event(code, "2026-07-01")] if page_matches else []
            return page_matches, events

        with patch("screening._fetch_nps_share_one", side_effect=fetch_one), self.assertLogs(
            "screening", level="WARNING"
        ) as logs:
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

        with patch("screening._fetch_nps_share_one", side_effect=fetch_one), self.assertLogs(
            "screening", level="WARNING"
        ) as logs:
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
        events.assert_called_once_with(
            holdings,
            require_coverage=True,
            verified_codes=set(),
        )
        reconcile.assert_called_once_with(
            holdings,
            [],
            None,
            as_of=date(2026, 7, 12),
            snapshot_inference_codes=set(),
        )

    def test_existing_state_allows_partial_share_analysis_coverage(self):
        from screening import build_nps_buy_signals

        previous = {
            "version": 1,
            "holdings": {"000001": {"종목명": "A", "보통주": 1000}},
            "signals": {},
        }
        holdings = [{"종목코드": "000001", "종목명": "A"}]
        with (
            patch("screening.load_nps_state", return_value=previous),
            patch("screening.fetch_nps_holdings", return_value=holdings) as snapshots,
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

        snapshots.assert_called_once_with(
            "ticker_map.json", required_codes={"000001"}
        )
        events.assert_called_once_with(
            holdings,
            require_coverage=False,
            verified_codes=set(),
        )


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

    def test_nps_signal_is_one_point_with_new_source_name(self):
        results, stats = calculate_scores(
            [{"종목명": "A"}],
            [{"종목명": "A"}],
            [
                {
                    "종목명": "A",
                    "매수구분": "추가매수",
                    "매수일": "2026-06-30",
                    "만료일": "2026-09-30",
                }
            ],
        )

        self.assertEqual(results[0]["종합점수"], 3)
        self.assertEqual(
            results[0]["출처"],
            "연간실적호전, 순매수전환, 국민연금 신규/추가매수",
        )
        self.assertEqual(results[0]["[연금]매수구분"], "추가매수")
        self.assertEqual(stats["nps_count"], 1)

    def test_expired_signal_removal_reduces_score_by_one(self):
        active, _ = calculate_scores(
            [{"종목명": "A"}], [{"종목명": "A"}], [{"종목명": "A"}]
        )
        expired, _ = calculate_scores(
            [{"종목명": "A"}], [{"종목명": "A"}], []
        )

        self.assertEqual(
            active[0]["종합점수"] - expired[0]["종합점수"],
            1,
        )
        self.assertNotIn("국민연금", expired[0]["출처"])


class SourceOrchestrationTest(unittest.TestCase):
    candidate_state: ClassVar[dict] = {
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

    def test_refresh_holds_state_lock_across_build_and_save(self):
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "nps_state.json"
            with (
                patch("screening.fetch_turnaround", return_value=[]),
                patch("screening.fetch_supply_trend", return_value=[]),
                patch(
                    "screening.build_nps_buy_signals",
                    return_value=([], self.candidate_state),
                ),
                patch("screening.nps_state_lock") as state_lock,
                patch("screening.save_nps_state") as save_state,
            ):
                fetch_all_data(
                    "ticker_map.json",
                    require_all=True,
                    nps_state_path=state_path,
                )

            state_lock.assert_called_once_with(state_path)
            state_lock.return_value.__enter__.assert_called_once_with()
            save_state.assert_called_once_with(state_path, self.candidate_state)
            state_lock.return_value.__exit__.assert_called_once()

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
                ),self.assertRaises(ScreeningDataError)
            ):
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
            self.assertRaisesRegex(ScreeningDataError, "상태 저장 실패"),
        ):
            fetch_all_data(require_all=True)


class SupplyTrendResilienceTest(unittest.TestCase):
    """일부 종목의 수급 조회가 실패해도 전체 소스가 죽지 않는지 검증한다."""

    @classmethod
    def setUpClass(cls):
        ticker_map_path = Path(__file__).resolve().parents[1] / "ticker_map.json"
        with open(ticker_map_path, encoding="utf-8") as file:
            cls.ticker_map = json.load(file)
        cls.stocks = [
            (name, code)
            for name, code in cls.ticker_map.items()
            if len(code) == 6 and code.isdigit()
        ][:10]
        self_check = len(cls.stocks)
        assert self_check == 10, f"티커 맵에서 10개 종목이 필요합니다: {self_check}"

    def fetch_with_failures(self, failing_positions):
        source_date = "2026-08-05"
        symbols = [f"A{code}" for _, code in self.stocks]
        failing_symbols = {symbols[position] for position in failing_positions}

        def rank_row(name, code):
            return {
                "rank": 1,
                "name": name,
                "symbolCode": f"A{code}",
                "code": f"KR7{code}001",
                "tradePrice": 100_000,
                "change": "RISE",
                "changeRate": 0.01,
                "changePrice": 1_000,
                "straightPurchaseVolume": 100,
                "straightPurchasePrice": 10_000_000,
            }

        def history_payload():
            return {
                "data": [
                    {
                        "date": f"{source_date} 00:00:00",
                        "foreignStraightPurchaseVolume": 100_000,
                        "institutionStraightPurchaseVolume": 50_000,
                        "tradePrice": 100_000,
                    },
                    {
                        "date": "2026-08-04 00:00:00",
                        "foreignStraightPurchaseVolume": -10,
                        "institutionStraightPurchaseVolume": -20,
                        "tradePrice": 99_000,
                    },
                ]
            }

        def route(url, kwargs):
            if url.endswith("/api/trend/investor_purchase"):
                params = kwargs["params"]
                rows = []
                if (params["market"], params["investorType"]) == (
                    "KOSPI",
                    "FOREIGN",
                ):
                    rows = [rank_row(name, code) for name, code in self.stocks]
                payload = {
                    "data": {"BUY": rows, "SELL": []},
                    "fromDate": source_date,
                    "toDate": source_date,
                }
                return FakeResponse(bom_json(payload))
            if url.endswith("/api/investor/days"):
                symbol_code = kwargs["params"]["symbolCode"]
                if symbol_code not in symbols:
                    raise AssertionError(f"예상하지 못한 심볼: {symbol_code}")
                if symbol_code in failing_symbols:
                    raise RuntimeError("일시적 네트워크 오류")
                return FakeResponse(bom_json(history_payload()))
            raise AssertionError(f"unexpected URL: {url}")

        return fetch_supply_trend(session=RoutingSession(route))

    def test_minor_history_failures_keep_the_remaining_candidates(self):
        # 10개 중 2개 실패 → 유효 8개 = 최소 기준(ceil(8)) 충족
        rows = self.fetch_with_failures({2, 7})

        self.assertEqual(len(rows), 8)
        self.assertEqual(rows[0]["No."], "1")

    def test_major_history_failures_fail_the_whole_source(self):
        # 10개 중 3개 실패 → 유효 7개 < 최소 기준(8)
        with self.assertRaises(ScreeningDataError):
            self.fetch_with_failures({1, 4, 9})


if __name__ == "__main__":
    unittest.main()
