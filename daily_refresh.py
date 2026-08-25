#!/usr/bin/env python3
"""macOS 예약 작업에서 호출하는 일일 스크리닝 갱신 진입점."""

import json
import logging
import time
from datetime import datetime
from datetime import time as dt_time
from urllib.error import URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from runtime_config import dashboard_url

KST = ZoneInfo("Asia/Seoul")
REFRESH_TIME = dt_time(7, 0)
DASHBOARD_URL = dashboard_url()
MAX_ATTEMPTS = 3
POLL_INTERVAL_SECONDS = 10
POLL_TIMEOUT_SECONDS = 600
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
    with urlopen(f"{DASHBOARD_URL}/api/status", timeout=5) as response:
        return json.load(response)


def _request_dashboard_refresh():
    request = Request(f"{DASHBOARD_URL}/api/refresh", data=b"", method="POST")
    with urlopen(request, timeout=5) as response:
        return json.load(response)


def _await_refresh_completion():
    """웹 서버의 갱신이 끝나거나(성공/실패) 시간이 다 되면 상태를 반환한다."""
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            status = _dashboard_status()
        except (OSError, URLError, ValueError) as exc:
            logger.warning("갱신 상태 조회 실패: %s", exc)
            return None
        if status.get("status") != "loading":
            return status
        time.sleep(POLL_INTERVAL_SECONDS)
    logger.warning("갱신 완료 대기 시간(%d초)을 초과했습니다", POLL_TIMEOUT_SECONDS)
    return None


def run(now=None):
    """실행 중인 웹 서버를 우선 사용하고, 없으면 직접 동기 갱신한다.

    웹 서버가 갱신을 마칠 때까지 기다려 결과를 확인하고, 오늘 데이터가
    준비될 때까지 최대 MAX_ATTEMPTS번까지 재요청한다. 이미 로딩 중이라는
    응답(already_loading)도 성공으로 치지 않고 완료를 확인한다.
    """
    if not refresh_is_due(None, now):
        logger.info("07:00 전이므로 예약 갱신을 건너뜁니다")
        return 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            status = _dashboard_status()
        except (OSError, URLError, ValueError):
            status = None

        if status is None:
            import app as app_module

            app_module.load_cache()
            with app_module.data_lock:
                last_updated = app_module.current_data.get("last_updated")
            if not refresh_is_due(last_updated, now):
                logger.info("오늘 데이터가 이미 갱신되어 예약 작업을 건너뜁니다")
                return 0
            if attempt > 1:
                logger.error(
                    "웹 서버에 연결할 수 없어 재시도 %d회차를 포기합니다", attempt
                )
                break
            logger.info("웹 서버가 없어 독립 프로세스에서 예약 갱신을 시작합니다")
            if app_module.refresh_data():
                return 0
            # 동기 갱신이 실패하면 남은 횟수 동안 다시 시도한다.
            for retry in range(2, MAX_ATTEMPTS + 1):
                time.sleep(POLL_INTERVAL_SECONDS)
                logger.info("독립 프로세스 갱신 재시도 (%d/%d)", retry, MAX_ATTEMPTS)
                if app_module.refresh_data():
                    return 0
            logger.error("예약 갱신을 완료하지 못했습니다 (%d회 시도)", MAX_ATTEMPTS)
            return 1

        if not refresh_is_due(status.get("last_updated"), now):
            logger.info("오늘 데이터가 이미 갱신되어 예약 작업을 건너뜁니다")
            return 0

        try:
            result = _request_dashboard_refresh()
        except (OSError, URLError, ValueError) as exc:
            logger.warning("갱신 요청 실패 (%d/%d): %s", attempt, MAX_ATTEMPTS, exc)
            result = {}

        if result.get("status") not in {"started", "already_loading"}:
            logger.warning(
                "웹 서버가 예약 갱신 요청을 거부했습니다 (%d/%d): %s",
                attempt,
                MAX_ATTEMPTS,
                result,
            )
        else:
            final_status = _await_refresh_completion()
            if final_status and not refresh_is_due(
                final_status.get("last_updated"), now
            ):
                logger.info("예약 갱신 완료: %s", final_status.get("last_updated"))
                return 0
            logger.warning(
                "갱신 후에도 데이터가 오래되었습니다 (%d/%d)",
                attempt,
                MAX_ATTEMPTS,
            )

        if attempt < MAX_ATTEMPTS:
            time.sleep(POLL_INTERVAL_SECONDS)

    logger.error("예약 갱신을 완료하지 못했습니다 (%d회 시도)", MAX_ATTEMPTS)
    return 1


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    raise SystemExit(run())
