import unittest

from runtime_config import daily_price_sync_enabled, dashboard_url, web_port


class RuntimeConfigTest(unittest.TestCase):
    def test_default_port_avoids_macos_airplay_receiver(self):
        self.assertEqual(web_port({}), 5050)
        self.assertEqual(dashboard_url({}), "http://127.0.0.1:5050")

    def test_dashboard_url_follows_the_configured_web_port(self):
        environment = {"GUKJANG_PORT": "5051"}

        self.assertEqual(web_port(environment), 5051)
        self.assertEqual(dashboard_url(environment), "http://127.0.0.1:5051")


class DailyPriceSyncFlagTest(unittest.TestCase):
    def test_price_sync_is_enabled_by_default(self):
        self.assertTrue(daily_price_sync_enabled({}))

    def test_price_sync_flag_accepts_common_spellings(self):
        for raw, expected in (
            ("0", False),
            ("false", False),
            ("no", False),
            ("off", False),
            ("1", True),
            ("true", True),
            ("yes", True),
            ("on", True),
            (" ON ", True),
        ):
            with self.subTest(raw=raw):
                self.assertEqual(
                    daily_price_sync_enabled({"GUKJANG_DAILY_PRICE_SYNC": raw}),
                    expected,
                )

    def test_unparseable_price_sync_flag_is_rejected(self):
        with self.assertRaises(ValueError):
            daily_price_sync_enabled({"GUKJANG_DAILY_PRICE_SYNC": "maybe"})


if __name__ == "__main__":
    unittest.main()
