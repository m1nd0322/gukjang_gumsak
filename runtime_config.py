"""웹 서버와 예약 작업이 공유하는 로컬 실행 설정."""

import os

DEFAULT_WEB_PORT = 5050


def web_port(environment=None):
    environment = os.environ if environment is None else environment
    return int(environment.get("GUKJANG_PORT", DEFAULT_WEB_PORT))


def dashboard_url(environment=None):
    environment = os.environ if environment is None else environment
    return environment.get(
        "GUKJANG_DASHBOARD_URL",
        f"http://127.0.0.1:{web_port(environment)}",
    )
