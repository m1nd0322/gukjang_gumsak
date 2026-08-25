"""웹 서버와 예약 작업이 공유하는 로컬 실행 설정."""

import os

DEFAULT_WEB_PORT = 5050
DEFAULT_DAILY_PRICE_SYNC = True


def web_port(environment=None):
    environment = os.environ if environment is None else environment
    return int(environment.get("GUKJANG_PORT", DEFAULT_WEB_PORT))


def dashboard_url(environment=None):
    environment = os.environ if environment is None else environment
    return environment.get(
        "GUKJANG_DASHBOARD_URL",
        f"http://127.0.0.1:{web_port(environment)}",
    )


def daily_price_sync_enabled(environment=None):
    """스크리닝 종목의 매일 가격 자동 동기화 사용 여부를 반환한다."""
    environment = os.environ if environment is None else environment
    raw = str(environment.get("GUKJANG_DAILY_PRICE_SYNC", "")).strip().lower()
    if not raw:
        return DEFAULT_DAILY_PRICE_SYNC
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    raise ValueError(f"GUKJANG_DAILY_PRICE_SYNC 값을 해석할 수 없습니다: {raw}")
