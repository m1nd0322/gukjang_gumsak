import unittest

from runtime_config import dashboard_url, web_port


class RuntimeConfigTest(unittest.TestCase):
    def test_default_port_avoids_macos_airplay_receiver(self):
        self.assertEqual(web_port({}), 5050)
        self.assertEqual(dashboard_url({}), "http://127.0.0.1:5050")

    def test_dashboard_url_follows_the_configured_web_port(self):
        environment = {"GUKJANG_PORT": "5051"}

        self.assertEqual(web_port(environment), 5051)
        self.assertEqual(dashboard_url(environment), "http://127.0.0.1:5051")


if __name__ == "__main__":
    unittest.main()
