#!/usr/bin/env python3
"""macOS 예약 작업에서 호출하는 일일 스크리닝 갱신 진입점."""

import json
import logging
import os
from datetime import datetime, time
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
REFRESH_TIME = time(7, 0)
DASHBOARD_URL = os.getenv("GUKJANG_DASHBOARD_URL", "http://127.0.0.1:5000")
logger = logging.getLogger(__name__)


def refresh_is_due(last_updated, now=None):
    """07:00 KST 이후이고 오늘 성공한 갱신이 없으면 True를 반환한다."""
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    else:
        now = now.astimezone(KST)

    if now.time() < REFRESH_TIME:
        return False

    try:
        updated_at = datetime.strptime(
            last_updated, "%Y-%m-%d %H:%M:%S"
        ).replace(tzinfo=KST)
    except (TypeError, ValueError):
        return True

    scheduled_at = datetime.combine(now.date(), REFRESH_TIME, tzinfo=KST)
    return updated_at < scheduled_at


def _dashboard_status():
    with urlopen(f"{DASHBOARD_URL}/api/status", timeout=2) as response:
        return json.load(response)


def _request_dashboard_refresh():
    request = Request(f"{DASHBOARD_URL}/api/refresh", data=b"", method="POST")
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def run(now=None):
    """실행 중인 웹 서버를 우선 사용하고, 없으면 직접 동기 갱신한다."""
    if not refresh_is_due(None, now):
        logger.info("07:00 전이므로 예약 갱신을 건너뜁니다")
        return 0

    try:
        status = _dashboard_status()
    except (OSError, URLError, ValueError):
        status = None

    if status is not None:
        if not refresh_is_due(status.get("last_updated"), now):
            logger.info("오늘 데이터가 이미 갱신되어 예약 작업을 건너뜁니다")
            return 0
        result = _request_dashboard_refresh()
        if result.get("status") in {"started", "already_loading"}:
            logger.info("웹 서버에 예약 갱신을 요청했습니다: %s", result["status"])
            return 0
        logger.error("웹 서버가 예약 갱신 요청을 거부했습니다: %s", result)
        return 1

    import app as app_module

    app_module.load_cache()
    with app_module.data_lock:
        last_updated = app_module.current_data.get("last_updated")
    if not refresh_is_due(last_updated, now):
        logger.info("오늘 데이터가 이미 갱신되어 예약 작업을 건너뜁니다")
        return 0

    logger.info("웹 서버가 없어 독립 프로세스에서 예약 갱신을 시작합니다")
    return 0 if app_module.refresh_data() else 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    raise SystemExit(run())
