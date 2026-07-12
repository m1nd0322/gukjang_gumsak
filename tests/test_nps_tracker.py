import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from nps_tracker import add_calendar_months, reconcile_nps_signals


class NpsTrackerTest(unittest.TestCase):
    def test_month_end_expiry(self):
        self.assertEqual(
            add_calendar_months(date(2026, 1, 31)),
            date(2026, 4, 30),
        )

    def test_bootstrap_scores_only_confirmed_recent_event(self):
        holdings = [
            {
                "종목코드": "000001",
                "종목명": "기존보유",
                "보통주": "1,000",
                "지분율(%)": "5.0",
                "최종변동일": "2025/01/01",
            },
            {
                "종목코드": "000002",
                "종목명": "신규종목",
                "보통주": "2,000",
                "지분율(%)": "6.0",
                "최종변동일": "2026/07/01",
            },
        ]
        events = [
            {
                "종목코드": "000002",
                "종목명": "신규종목",
                "변동일": "2026-07-01",
                "변동사유": "신규주요주주(+)",
                "변동전": 1500,
                "증감": 500,
                "변동후": 2000,
                "지분율(%)": 6.0,
            }
        ]

        active, state = reconcile_nps_signals(
            holdings, events, None, as_of=date(2026, 7, 12)
        )

        self.assertEqual([row["종목코드"] for row in active], ["000002"])
        self.assertEqual(active[0]["매수구분"], "신규매수")
        self.assertEqual(active[0]["만료일"], "2026-10-01")
        self.assertEqual(set(state["holdings"]), {"000001", "000002"})

    def test_signal_is_removed_on_expiry_date(self):
        holdings = [
            {
                "종목코드": "000001",
                "종목명": "경계종목",
                "보통주": "1,000",
                "지분율(%)": "5.0",
                "최종변동일": "2026/04/12",
            }
        ]
        previous_state = {
            "version": 1,
            "updated_at": "2026-07-10",
            "holdings": {
                "000001": {
                    "종목명": "경계종목",
                    "보통주": 1000,
                    "지분율": 5.0,
                    "최종변동일": "2026-04-12",
                }
            },
            "signals": {
                "000001": {
                    "종목명": "경계종목",
                    "매수구분": "신규매수",
                    "매수일": "2026-04-12",
                    "만료일": "2026-07-12",
                    "변동사유": "신규주요주주(+)",
                    "변동전": 0,
                    "증감": 1000,
                    "변동후": 1000,
                    "지분율": 5.0,
                }
            },
        }

        active_before, _ = reconcile_nps_signals(
            holdings, [], previous_state, as_of=date(2026, 7, 11)
        )
        active_on_expiry, _ = reconcile_nps_signals(
            holdings, [], previous_state, as_of=date(2026, 7, 12)
        )

        self.assertEqual([row["종목코드"] for row in active_before], ["000001"])
        self.assertEqual(active_on_expiry, [])

    def test_latest_additional_buy_resets_expiry_without_stacking(self):
        holdings = [
            {
                "종목코드": "000001",
                "종목명": "추가매수종목",
                "보통주": "1,500",
                "지분율(%)": "7.5",
                "최종변동일": "2026/06/30",
            }
        ]
        events = [
            {
                "종목코드": "000001",
                "변동일": "2026-06-30",
                "변동사유": "장내매수(+)",
                "변동전": 1000,
                "증감": 500,
                "변동후": 1500,
                "지분율(%)": 7.5,
            },
            {
                "종목코드": "000001",
                "변동일": "2026-06-01",
                "변동사유": "장내매수(+)",
                "변동전": 900,
                "증감": 100,
                "변동후": 1000,
                "지분율(%)": 5.0,
            },
        ]

        active, state = reconcile_nps_signals(
            holdings, events, None, as_of=date(2026, 7, 12)
        )

        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["매수구분"], "추가매수")
        self.assertEqual(active[0]["매수일"], "2026-06-30")
        self.assertEqual(active[0]["만료일"], "2026-09-30")
        self.assertEqual(len(state["signals"]), 1)

    def test_new_buy_wins_when_events_share_the_same_date(self):
        holdings = [
            {
                "종목코드": "000001",
                "종목명": "동일일자종목",
                "보통주": "1,000",
                "지분율(%)": "5.0",
                "최종변동일": "2026/07/01",
            }
        ]
        events = [
            {
                "종목코드": "000001",
                "변동일": "2026-07-01",
                "변동사유": "신규주요주주(+)",
                "변동전": 0,
                "증감": 900,
                "변동후": 900,
            },
            {
                "종목코드": "000001",
                "변동일": "2026-07-01",
                "변동사유": "장내매수(+)",
                "변동전": 900,
                "증감": 100,
                "변동후": 1000,
            },
        ]

        active, _ = reconcile_nps_signals(
            holdings, events, None, as_of=date(2026, 7, 12)
        )

        self.assertEqual(active[0]["매수구분"], "신규매수")
        self.assertEqual(active[0]["변동사유"], "신규주요주주(+)")

    def test_negative_event_does_not_refresh_original_buy_date(self):
        holdings = [
            {
                "종목코드": "000001",
                "종목명": "매도종목",
                "보통주": "900",
                "지분율(%)": "4.5",
                "최종변동일": "2026/07/01",
            }
        ]
        previous_state = {
            "version": 1,
            "updated_at": "2026-06-01",
            "holdings": {
                "000001": {
                    "종목명": "매도종목",
                    "보통주": 1000,
                    "지분율": 5.0,
                    "최종변동일": "2026-06-01",
                }
            },
            "signals": {
                "000001": {
                    "종목명": "매도종목",
                    "매수구분": "추가매수",
                    "매수일": "2026-06-01",
                    "만료일": "2026-09-01",
                    "변동사유": "장내매수(+)",
                    "변동전": 800,
                    "증감": 200,
                    "변동후": 1000,
                    "지분율": 5.0,
                }
            },
        }
        events = [
            {
                "종목코드": "000001",
                "변동일": "2026-07-01",
                "변동사유": "장내매도(-)",
                "변동전": 1000,
                "증감": -100,
                "변동후": 900,
            }
        ]

        active, _ = reconcile_nps_signals(
            holdings, events, previous_state, as_of=date(2026, 7, 12)
        )

        self.assertEqual(active[0]["매수일"], "2026-06-01")
        self.assertEqual(active[0]["만료일"], "2026-09-01")

    def test_disappeared_holding_removes_signal(self):
        previous_state = {
            "version": 1,
            "updated_at": "2026-07-01",
            "holdings": {"000001": {"보통주": 1000}},
            "signals": {
                "000001": {
                    "종목명": "청산종목",
                    "매수구분": "신규매수",
                    "매수일": "2026-07-01",
                    "만료일": "2026-10-01",
                    "변동사유": "신규주요주주(+)",
                    "변동전": 0,
                    "증감": 1000,
                    "변동후": 1000,
                    "지분율": 5.0,
                }
            },
        }

        active, state = reconcile_nps_signals(
            [], [], previous_state, as_of=date(2026, 7, 12)
        )

        self.assertEqual(active, [])
        self.assertEqual(state["signals"], {})

    def test_new_snapshot_holding_creates_new_buy_after_baseline_exists(self):
        holdings = [
            {
                "종목코드": "000001",
                "종목명": "기존종목",
                "보통주": "1,000",
                "지분율(%)": "5.0",
                "최종변동일": "2026/01/01",
            },
            {
                "종목코드": "000002",
                "종목명": "새종목",
                "보통주": "2,000",
                "지분율(%)": "6.0",
                "최종변동일": "2026/07/05",
            },
        ]
        previous_state = {
            "version": 1,
            "updated_at": "2026-07-04",
            "holdings": {
                "000001": {
                    "종목명": "기존종목",
                    "보통주": 1000,
                    "지분율": 5.0,
                    "최종변동일": "2026-01-01",
                }
            },
            "signals": {},
        }

        active, state = reconcile_nps_signals(
            holdings, [], previous_state, as_of=date(2026, 7, 12)
        )

        self.assertEqual([row["종목코드"] for row in active], ["000002"])
        self.assertEqual(active[0]["매수구분"], "신규매수")
        self.assertEqual(active[0]["매수일"], "2026-07-05")
        self.assertEqual(active[0]["변동사유"], "Snapshot 신규 보유")
        self.assertEqual(set(state["holdings"]), {"000001", "000002"})

    def test_snapshot_share_increase_with_later_date_creates_additional_buy(self):
        holdings = [
            {
                "종목코드": "000001",
                "종목명": "증가종목",
                "보통주": "1,200",
                "지분율(%)": "6.0",
                "최종변동일": "2026/07/01",
            }
        ]
        previous_state = {
            "version": 1,
            "updated_at": "2026-06-01",
            "holdings": {
                "000001": {
                    "종목명": "증가종목",
                    "보통주": 1000,
                    "지분율": 5.0,
                    "최종변동일": "2026-06-01",
                }
            },
            "signals": {},
        }

        active, _ = reconcile_nps_signals(
            holdings, [], previous_state, as_of=date(2026, 7, 12)
        )

        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["매수구분"], "추가매수")
        self.assertEqual(active[0]["매수일"], "2026-07-01")
        self.assertEqual(active[0]["만료일"], "2026-10-01")
        self.assertEqual(active[0]["변동사유"], "Snapshot 보유량 증가")
        self.assertEqual(active[0]["증감"], "200")

    def test_snapshot_requires_both_more_shares_and_a_later_date(self):
        previous_state = {
            "version": 1,
            "updated_at": "2026-06-01",
            "holdings": {
                "000001": {
                    "종목명": "변경종목",
                    "보통주": 1000,
                    "지분율": 5.0,
                    "최종변동일": "2026-06-01",
                }
            },
            "signals": {},
        }
        cases = [
            ("1,000", "2026/07/01"),
            ("1,200", "2026/06/01"),
        ]
        for shares, changed_at in cases:
            with self.subTest(shares=shares, changed_at=changed_at):
                holdings = [
                    {
                        "종목코드": "000001",
                        "종목명": "변경종목",
                        "보통주": shares,
                        "지분율(%)": "6.0",
                        "최종변동일": changed_at,
                    }
                ]

                active, _ = reconcile_nps_signals(
                    holdings, [], previous_state, as_of=date(2026, 7, 12)
                )

                self.assertEqual(active, [])

    def test_missing_state_file_returns_none(self):
        from nps_tracker import load_nps_state

        with TemporaryDirectory() as directory:
            self.assertIsNone(load_nps_state(Path(directory) / "missing.json"))

    def test_invalid_json_raises_state_error(self):
        from nps_tracker import NpsStateError, load_nps_state

        with TemporaryDirectory() as directory:
            path = Path(directory) / "nps_state.json"
            path.write_text("{broken", encoding="utf-8")

            with self.assertRaisesRegex(NpsStateError, "상태 파일 오류"):
                load_nps_state(path)

    def test_invalid_state_schema_is_rejected(self):
        from nps_tracker import NpsStateError, load_nps_state

        invalid_states = [
            "[]",
            '{"version": 2, "holdings": {}, "signals": {}}',
            '{"version": 1, "holdings": [], "signals": {}}',
            '{"version": 1, "holdings": {}, "signals": []}',
        ]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "nps_state.json"
            for invalid_state in invalid_states:
                with self.subTest(state=invalid_state):
                    path.write_text(invalid_state, encoding="utf-8")
                    with self.assertRaises(NpsStateError):
                        load_nps_state(path)

    def test_atomic_save_load_round_trip_preserves_state(self):
        from nps_tracker import load_nps_state, save_nps_state

        state = {
            "version": 1,
            "updated_at": "2026-07-12",
            "holdings": {"000001": {"보통주": 1000}},
            "signals": {},
        }
        with TemporaryDirectory() as directory:
            path = Path(directory) / "nps_state.json"

            save_nps_state(path, state)

            self.assertEqual(load_nps_state(path), state)
            self.assertEqual(list(Path(directory).glob(".nps-state-*.json")), [])

    def test_today_uses_fixed_korea_timezone(self):
        from nps_tracker import kst_today

        korea_now = datetime(
            2026, 7, 12, 0, 30, tzinfo=timezone(timedelta(hours=9))
        )
        with patch("nps_tracker.datetime") as datetime_type:
            datetime_type.now.return_value = korea_now

            self.assertEqual(kst_today(), date(2026, 7, 12))

        korea_timezone = datetime_type.now.call_args.args[0]
        self.assertEqual(korea_timezone.utcoffset(None), timedelta(hours=9))
        self.assertEqual(korea_timezone.tzname(None), "Asia/Seoul")


if __name__ == "__main__":
    unittest.main()
