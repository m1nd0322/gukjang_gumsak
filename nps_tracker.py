"""국민연금 신규·추가매수 신호의 날짜와 상태 전이."""

from calendar import monthrange
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import json
import os
import tempfile
import time


STATE_VERSION = 1
KST = timezone(timedelta(hours=9), name="Asia/Seoul")


class NpsStateError(ValueError):
    """저장된 국민연금 상태를 안전하게 사용할 수 없을 때 발생한다."""


class NpsStateLockError(NpsStateError):
    """다른 프로세스가 상태 갱신을 끝내지 않아 잠금을 얻지 못한 경우."""


@contextmanager
def nps_state_lock(
    path: str | os.PathLike,
    *,
    timeout: float = 30,
    stale_after: float = 900,
):
    """원자적 디렉터리 생성으로 OS에 독립적인 프로세스 잠금을 건다."""
    lock_path = f"{os.path.abspath(os.fspath(path))}.lock"
    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        try:
            os.mkdir(lock_path)
            break
        except FileExistsError:
            try:
                lock_age = time.time() - os.path.getmtime(lock_path)
            except FileNotFoundError:
                continue
            if lock_age >= max(0.0, float(stale_after)):
                try:
                    os.rmdir(lock_path)
                except OSError:
                    pass
                else:
                    continue
            if time.monotonic() >= deadline:
                raise NpsStateLockError(
                    f"국민연금 상태 갱신 잠금을 얻지 못했습니다: {lock_path}"
                )
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    try:
        yield
    finally:
        try:
            os.rmdir(lock_path)
        except FileNotFoundError:
            pass


def load_nps_state(path: str | os.PathLike) -> dict | None:
    """버전과 기본 구조를 검증해 상태 파일을 읽는다."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as file:
            state = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise NpsStateError(f"국민연금 상태 파일 오류: {exc}") from exc
    if not isinstance(state, dict):
        raise NpsStateError("국민연금 상태 구조가 올바르지 않습니다")
    if state.get("version") != STATE_VERSION:
        raise NpsStateError("지원하지 않는 국민연금 상태 버전입니다")
    if not isinstance(state.get("holdings"), dict) or not isinstance(
        state.get("signals"), dict
    ):
        raise NpsStateError("국민연금 상태 구조가 올바르지 않습니다")
    return state


def save_nps_state(path: str | os.PathLike, state: dict) -> None:
    """완성된 상태를 같은 디렉터리의 임시 파일을 거쳐 원자적으로 교체한다."""
    directory = os.path.dirname(os.path.abspath(path))
    descriptor, temporary = tempfile.mkstemp(
        prefix=".nps-state-", suffix=".json", dir=directory
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(state, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def kst_today() -> date:
    """호스트 OS의 로컬 시간대와 무관한 한국 날짜를 반환한다."""
    return datetime.now(KST).date()


def _parse_int(value) -> int:
    return int(str(value or "").replace(",", "").strip() or 0)


def _parse_float(value) -> float:
    return float(str(value or "").replace(",", "").strip() or 0)


def _parse_date(value) -> date | None:
    text = str(value or "").strip().replace("/", "-").replace(".", "-")
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def add_calendar_months(value: date, months: int = 3) -> date:
    """월말을 보정하면서 달력 기준 개월 수를 더한다."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def _normalize_holdings(holdings: list[dict]) -> dict[str, dict]:
    normalized = {}
    for row in holdings:
        code = str(row.get("종목코드") or "").strip()
        if not code:
            continue
        changed_at = _parse_date(row.get("최종변동일"))
        normalized[code] = {
            "종목명": str(row.get("종목명") or "").strip(),
            "보통주": _parse_int(row.get("보통주")),
            "지분율": _parse_float(row.get("지분율(%)")),
            "최종변동일": changed_at.isoformat() if changed_at else "",
        }
    return normalized


def reconcile_nps_signals(
    holdings: list[dict],
    events: list[dict],
    previous_state: dict | None,
    *,
    as_of: date,
    snapshot_inference_codes: set[str] | None = None,
) -> tuple[list[dict], dict]:
    """현재 보유와 확인된 매수 이벤트를 활성 신호로 병합한다."""
    current = _normalize_holdings(holdings)
    signals = {}
    disclosed_event_dates = set()
    for event in events:
        event_code = str(event.get("종목코드") or "").strip()
        event_date = _parse_date(event.get("변동일"))
        if event_code and event_date:
            disclosed_event_dates.add((event_code, event_date))

    previous_signals = (previous_state or {}).get("signals", {})
    if isinstance(previous_signals, dict):
        for code, signal in previous_signals.items():
            bought_on = _parse_date(signal.get("매수일"))
            expires_on = _parse_date(signal.get("만료일"))
            if (
                code in current
                and bought_on
                and expires_on
                and bought_on <= as_of < expires_on
            ):
                signals[code] = dict(signal)

    candidate_events = []
    previous_holdings = (previous_state or {}).get("holdings", {})
    if previous_state is not None and isinstance(previous_holdings, dict):
        for code, holding in current.items():
            if (
                snapshot_inference_codes is not None
                and code not in snapshot_inference_codes
            ):
                continue
            previous_holding = previous_holdings.get(code)
            current_date = _parse_date(holding["최종변동일"])
            has_disclosed_current_event = (
                code,
                current_date,
            ) in disclosed_event_dates
            if previous_holding is None:
                if not has_disclosed_current_event:
                    candidate_events.append(
                        {
                            "종목코드": code,
                            "변동일": holding["최종변동일"],
                            "변동사유": "Snapshot 신규 보유",
                            "변동전": 0,
                            "증감": holding["보통주"],
                            "변동후": holding["보통주"],
                            "지분율(%)": holding["지분율"],
                        }
                    )
                continue
            if not isinstance(previous_holding, dict):
                continue
            previous_shares = _parse_int(previous_holding.get("보통주"))
            previous_date = _parse_date(previous_holding.get("최종변동일"))
            if (
                holding["보통주"] > previous_shares
                and previous_date
                and current_date
                and current_date > previous_date
                and not has_disclosed_current_event
            ):
                candidate_events.append(
                    {
                        "종목코드": code,
                        "변동일": holding["최종변동일"],
                        "변동사유": "Snapshot 보유량 증가",
                        "변동전": previous_shares,
                        "증감": holding["보통주"] - previous_shares,
                        "변동후": holding["보통주"],
                        "지분율(%)": holding["지분율"],
                    }
                )

    candidate_events.extend(events)

    for event in candidate_events:
        code = str(event.get("종목코드") or "").strip()
        event_date = _parse_date(event.get("변동일"))
        change = _parse_int(event.get("증감"))
        before = _parse_int(event.get("변동전"))
        after = _parse_int(event.get("변동후"))
        if (
            code not in current
            or not event_date
            or change <= 0
            or after <= before
            or after - before != change
        ):
            continue
        reason = str(event.get("변동사유") or "")
        if "(-)" in reason or "매도" in reason:
            continue
        buy_type = "신규매수" if reason.startswith("신규") or before == 0 else "추가매수"
        expires_on = add_calendar_months(event_date)
        if not event_date <= as_of < expires_on:
            continue
        existing_signal = signals.get(code, {})
        existing_date = _parse_date(existing_signal.get("매수일"))
        if existing_date and existing_date > event_date:
            continue
        if (
            existing_date == event_date
            and existing_signal.get("매수구분") == "신규매수"
            and buy_type != "신규매수"
        ):
            continue
        signals[code] = {
            "종목명": current[code]["종목명"],
            "매수구분": buy_type,
            "매수일": event_date.isoformat(),
            "만료일": expires_on.isoformat(),
            "변동사유": reason,
            "변동전": before,
            "증감": change,
            "변동후": after,
            "지분율": _parse_float(event.get("지분율(%)")),
        }

    active = []
    for code, signal in signals.items():
        holding = current[code]
        active.append(
            {
                "No.": str(len(active) + 1),
                "종목코드": code,
                "종목명": holding["종목명"],
                "보통주": f'{holding["보통주"]:,}',
                "지분율(%)": f'{holding["지분율"]:.2f}',
                "최종변동일": holding["최종변동일"].replace("-", "/"),
                "매수구분": signal["매수구분"],
                "매수일": signal["매수일"],
                "만료일": signal["만료일"],
                "변동사유": signal["변동사유"],
                "변동전": f'{signal["변동전"]:,}',
                "증감": f'{signal["증감"]:,}',
                "변동후": f'{signal["변동후"]:,}',
            }
        )

    return active, {
        "version": STATE_VERSION,
        "updated_at": as_of.isoformat(),
        "holdings": current,
        "signals": signals,
    }
