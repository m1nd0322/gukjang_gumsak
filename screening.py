"""공개 스크리닝 데이터 수집과 종합 점수 계산.

구형 WooriRenewal HTML 화면은 2026-06-29부터 오류 문서를 반환한다.
2026-07-29 종료된 구형 FnGuide JSON 피드 대신 신버전 FnGuide
턴어라운드 API, Daum 일별 수급, FnGuide Snapshot/ShareAnalysis를 읽어
웹 앱, 정적 리포트, 일일 리포트가 같은 데이터 계층을 사용하게 한다.
"""

import json
import logging
import math
import os
import re
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from html import unescape

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from nps_tracker import (
    NpsStateLockError,
    kst_today,
    load_nps_state,
    nps_state_lock,
    reconcile_nps_signals,
    save_nps_state,
)

logger = logging.getLogger(__name__)

TURNAROUND_URL = "https://wcomp.fnguide.com/Consensus/getScrEarTrn"
SUPPLY_TREND_URL = "https://finance.daum.net/api/trend/investor_purchase"
SUPPLY_HISTORY_URL = "https://finance.daum.net/api/investor/days"
SNAPSHOT_URL = "https://wcomp.fnguide.com/CompanyInfo/Snapshot"
SHARE_ANALYSIS_URL = "https://wcomp.fnguide.com/CompanyInfo/ShareAnalysis"
DEFAULT_TICKER_MAP = os.path.join(os.path.dirname(__file__), "ticker_map.json")
DEFAULT_NPS_STATE = os.path.join(os.path.dirname(__file__), "nps_state.json")
NPS_SOURCE_LABEL = "국민연금 신규/추가매수"

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; gukjang-gumsak/1.0; "
        "+https://github.com/m1nd0322/gukjang_gumsak)"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
}
DAUM_FINANCE_HEADERS = {
    **REQUEST_HEADERS,
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://finance.daum.net/domestic/influential_investors",
}

_TITLE_TICKER_RE = re.compile(r"<title[^>]*>.*?\(([^()]+)\)\s*\|", re.IGNORECASE | re.DOTALL)
_NPS_ROW_RE = re.compile(
    r"<th[^>]*\btitle=[\"']국민연금공단[\"'][^>]*>.*?</th>\s*"
    r"<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*"
    r"<td[^>]*>(.*?)</td>",
    re.IGNORECASE | re.DOTALL,
)
_NPS_HOLDER_MARKER_RE = re.compile(
    r'<th[^>]*\btitle=["\']국민연금공단["\'][^>]*>', re.IGNORECASE | re.DOTALL
)
_SHARE_BODY_RE = re.compile(
    r'<tbody[^>]*id=["\']sharebody["\'][^>]*>(.*?)</tbody>', re.IGNORECASE | re.DOTALL
)
_HTML_TABLE_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_HTML_CAPTION_RE = re.compile(r"<caption\b[^>]*>(.*?)</caption>", re.IGNORECASE | re.DOTALL)
_SHARE_CHANGE_TABLE_ID_RE = re.compile(
    r'<table\b[^>]*\bid\s*=\s*["\']tbl_own_chg["\']', re.IGNORECASE | re.DOTALL
)
_HTML_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_HTML_CELL_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_thread_local = threading.local()


class ScreeningDataError(RuntimeError):
    """스크리닝 원천 데이터가 유효하지 않을 때 발생한다."""


def normalize_stock_name(name: object) -> str:
    """종목명의 앞뒤 및 연속 공백을 정규화한다."""
    return re.sub(r"\s+", " ", str(name or "").strip())


def _text_or_empty(value: object) -> str:
    return "" if value is None else str(value)


def _retry_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.2,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=4))
    session.headers.update(REQUEST_HEADERS)
    return session


def _worker_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = _retry_session()
        _thread_local.session = session
    return session


def fetch_turnaround(
    *, session: requests.Session | None = None, timeout: float = 20
) -> list[dict]:
    """연간 영업이익 흑자전환 종목을 FnGuide 신버전 API에서 읽는다."""
    client = session or _retry_session()
    params = {"prc": 1, "consol_typ": "C", "fin_typ": "O", "freq_typ": "Y"}
    try:
        response = client.get(
            TURNAROUND_URL,
            params=params,
            headers=REQUEST_HEADERS,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = json.loads(response.content.decode("utf-8-sig"))
    except Exception as exc:
        raise ScreeningDataError(
            f"FnGuide 턴어라운드 응답 해석 실패 ({TURNAROUND_URL}): {exc}"
        ) from exc

    dataset = payload.get("dataset") if isinstance(payload, dict) else None
    raw_rows = dataset.get("data") if isinstance(dataset, dict) else None
    if not isinstance(raw_rows, list):
        raise ScreeningDataError(
            f"FnGuide 턴어라운드 응답 구조가 올바르지 않습니다 ({TURNAROUND_URL})"
        )

    frequency_labels = {"Annual": "연간", "Quarter": "분기"}
    rows = []
    for index, raw in enumerate(raw_rows, start=1):
        if not isinstance(raw, dict):
            continue
        stock_name = normalize_stock_name(raw.get("CMP_KOR"))
        if not stock_name:
            continue
        rows.append(
            {
                "No.": str(index),
                "종목명": stock_name,
                "결산년월": frequency_labels.get(
                    str(raw.get("FREQ") or ""), str(raw.get("FREQ") or "")
                ),
                "최근결산 영업이익": _text_or_empty(raw.get("CUR_VAL")),
                "직전결산 영업이익": _text_or_empty(raw.get("BEF_VAL")),
                "증가율": _text_or_empty(raw.get("GROWTH")),
                "PER": "",
                "PBR": "",
            }
        )
    logger.info("턴어라운드: %d개 종목", len(rows))
    return rows


def fetch_supply_trend(
    *,
    session: requests.Session | None = None,
    timeout: float = 20,
    ticker_map_path: str = DEFAULT_TICKER_MAP,
) -> list[dict]:
    """Daum 일별 수급으로 외국인/기관 동반 순매수 전환을 판정한다."""
    try:
        with open(ticker_map_path, encoding="utf-8") as file:
            ticker_map = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ScreeningDataError(f"종목 코드 맵을 읽을 수 없습니다: {exc}") from exc
    if not isinstance(ticker_map, dict) or not ticker_map:
        raise ScreeningDataError("종목 코드 맵이 비어 있거나 올바르지 않습니다")

    code_to_name = {
        str(code).strip().upper().removeprefix("A"): normalize_stock_name(name)
        for name, code in ticker_map.items()
        if normalize_stock_name(name) and str(code).strip()
    }
    client = session or _retry_session()
    candidates: dict[str, dict] = {}
    source_dates = set()
    common_params = {
        "limit": 30,
        "intervalType": "TODAY",
        "buyFieldName": "straightPurchasePrice",
        "buyOrder": "desc",
        "sellFieldName": "straightPurchasePrice",
        "sellOrder": "asc",
    }

    for market in ("KOSPI", "KOSDAQ"):
        for investor_type in ("FOREIGN", "INSTITUTION"):
            params = {
                **common_params,
                "market": market,
                "investorType": investor_type,
            }
            try:
                response = client.get(
                    SUPPLY_TREND_URL,
                    params=params,
                    headers=DAUM_FINANCE_HEADERS,
                    timeout=timeout,
                )
                response.raise_for_status()
                payload = json.loads(response.content.decode("utf-8-sig"))
            except Exception as exc:
                raise ScreeningDataError(
                    f"Daum 순매수 상위 응답 해석 실패 ({market}/{investor_type}): {exc}"
                ) from exc

            data = payload.get("data") if isinstance(payload, dict) else None
            buy_rows = data.get("BUY") if isinstance(data, dict) else None
            source_date = str(payload.get("toDate") or "")[:10]
            if not isinstance(buy_rows, list) or not source_date:
                raise ScreeningDataError(
                    f"Daum 순매수 상위 응답 구조가 올바르지 않습니다 "
                    f"({market}/{investor_type})"
                )
            source_dates.add(source_date)

            for raw in buy_rows:
                if not isinstance(raw, dict):
                    continue
                symbol_code = str(raw.get("symbolCode") or "").strip().upper()
                code = symbol_code.removeprefix("A")
                if code not in code_to_name:
                    continue
                candidates.setdefault(
                    symbol_code,
                    {
                        "code": code,
                        "name": code_to_name[code],
                        "change_rate": raw.get("changeRate"),
                    },
                )

    if len(source_dates) != 1:
        raise ScreeningDataError(
            "Daum 순매수 상위 응답의 기준일이 일치하지 않습니다"
        )
    source_date = source_dates.pop()

    def fetch_history(symbol_code: str) -> list[dict]:
        history_client = client if session is not None else _worker_session()
        params = {"symbolCode": symbol_code, "page": 1, "perPage": 2}
        try:
            response = history_client.get(
                SUPPLY_HISTORY_URL,
                params=params,
                headers=DAUM_FINANCE_HEADERS,
                timeout=timeout,
            )
            response.raise_for_status()
            payload = json.loads(response.content.decode("utf-8-sig"))
        except Exception as exc:
            raise ScreeningDataError(
                f"Daum 종목별 수급 응답 해석 실패 ({symbol_code}): {exc}"
            ) from exc
        history = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(history, list):
            raise ScreeningDataError(
                f"Daum 종목별 수급 응답 구조가 올바르지 않습니다 ({symbol_code})"
            )
        return history

    def fetch_history_safe(symbol_code: str):
        try:
            return symbol_code, fetch_history(symbol_code)
        except Exception as exc:  # noqa: BLE001
            return symbol_code, exc

    histories: dict[str, list[dict]] = {}
    history_failures = 0
    if session is not None:
        for symbol_code in candidates:
            code, result = fetch_history_safe(symbol_code)
            if isinstance(result, Exception):
                history_failures += 1
                logger.warning(
                    "Daum 종목별 수급 조회 실패 (%s): %s", code, result
                )
            else:
                histories[code] = result
    else:
        with ThreadPoolExecutor(max_workers=min(8, max(1, len(candidates)))) as executor:
            futures = {
                executor.submit(fetch_history_safe, symbol_code): symbol_code
                for symbol_code in candidates
            }
            for future in as_completed(futures):
                symbol_code, result = future.result()
                if isinstance(result, Exception):
                    history_failures += 1
                    logger.warning(
                        "Daum 종목별 수급 조회 실패 (%s): %s", symbol_code, result
                    )
                else:
                    histories[symbol_code] = result

    minimum_valid_histories = math.ceil(len(candidates) * 0.8)
    if len(histories) < minimum_valid_histories:
        raise ScreeningDataError(
            "Daum 종목별 수급 유효 응답 비율이 낮습니다 "
            f"({len(histories)}/{len(candidates)}, 최소 {minimum_valid_histories}, "
            f"실패 {history_failures})"
        )

    selected = []
    date_mismatches = 0
    for symbol_code, candidate in candidates.items():
        history = histories.get(symbol_code)
        if (
            not history
            or len(history) < 2
            or not all(isinstance(row, dict) for row in history[:2])
        ):
            continue
        current, previous = history[:2]
        if str(current.get("date") or "")[:10] != source_date:
            date_mismatches += 1
            logger.debug(
                "Daum 순매수 상위/종목별 수급의 기준일이 다릅니다 (%s)", symbol_code
            )
            continue
        try:
            current_foreign = float(current["foreignStraightPurchaseVolume"])
            current_institution = float(current["institutionStraightPurchaseVolume"])
            previous_foreign = float(previous["foreignStraightPurchaseVolume"])
            previous_institution = float(previous["institutionStraightPurchaseVolume"])
            trade_price = float(current["tradePrice"])
            change_rate = float(candidate["change_rate"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ScreeningDataError(
                f"Daum 종목별 수급 값이 올바르지 않습니다 ({symbol_code}): {exc}"
            ) from exc

        current_joint_buy = current_foreign > 0 and current_institution > 0
        previous_joint_buy = previous_foreign > 0 and previous_institution > 0
        if not current_joint_buy or previous_joint_buy:
            continue

        estimated_amount = (
            trade_price * (current_foreign + current_institution) / 100_000_000
        )
        selected.append(
            (
                estimated_amount,
                {
                    "종목명": candidate["name"],
                    "전일종가(원)": f"{trade_price:,.0f}",
                    "수익률(%)": f"{change_rate * 100:.1f}",
                    "순매수금액(억원)": f"{estimated_amount:,.1f}",
                },
            )
        )

    selected.sort(key=lambda item: item[0], reverse=True)
    rows = []
    for index, (_, row) in enumerate(selected[:30], start=1):
        rows.append({"No.": str(index), **row})
    logger.info(
        "순매수전환: %d개 종목 (조회 실패 %d, 기준일 불일치 제외 %d)",
        len(rows),
        history_failures,
        date_mismatches,
    )
    return rows


def _cell_text(value: str) -> str:
    return normalize_stock_name(unescape(_TAG_RE.sub("", value)).replace("\xa0", " "))


def _snapshot_ticker(html: str) -> str | None:
    title_match = _TITLE_TICKER_RE.search(html or "")
    return title_match.group(1).strip().upper() if title_match else None


def _table_has_caption(table_html: str, expected: str) -> bool:
    return any(
        _cell_text(caption) == expected
        for caption in _HTML_CAPTION_RE.findall(table_html or "")
    )


def _has_snapshot_shareholder_table(html: str) -> bool:
    shareholder_tables = [
        table_html
        for table_html in _HTML_TABLE_RE.findall(html or "")
        if _table_has_caption(table_html, "주주현황")
    ]
    if not shareholder_tables:
        return False

    for table_html in shareholder_tables:
        if not _NPS_HOLDER_MARKER_RE.search(table_html):
            continue
        row_match = _NPS_ROW_RE.search(table_html)
        if not row_match:
            return False
        common_shares, ratio, changed_at = (
            _cell_text(value) for value in row_match.groups()
        )
        try:
            if int(common_shares.replace(",", "")) <= 0 or float(ratio) <= 0:
                return False
            date.fromisoformat(changed_at.replace("/", "-").replace(".", "-"))
        except ValueError:
            return False
    return True


def _share_change_rows_are_valid(table_html: str) -> bool:
    body_match = _SHARE_BODY_RE.search(table_html or "")
    if not body_match:
        return False
    row_html_values = _HTML_ROW_RE.findall(body_match.group(1))
    if not row_html_values:
        return True

    no_data_labels = {"", "-", "자료가 없습니다", "데이터가 없습니다"}
    for row_html in row_html_values:
        cells = [_cell_text(cell) for cell in _HTML_CELL_RE.findall(row_html)]
        if len(cells) == 1 and cells[0] in no_data_labels:
            continue
        if len(cells) < 10:
            return False
        try:
            date.fromisoformat(cells[3].replace("/", "-").replace(".", "-"))
            int(cells[6].replace(",", ""))
            int(cells[7].replace(",", ""))
            int(cells[8].replace(",", ""))
            float(cells[9].replace(",", ""))
        except ValueError:
            return False
    return True


def _has_share_change_table(html: str) -> bool:
    for table_html in _HTML_TABLE_RE.findall(html or ""):
        if (
            _SHARE_CHANGE_TABLE_ID_RE.search(table_html)
            and _table_has_caption(table_html, "주주변동내역")
            and _share_change_rows_are_valid(table_html)
        ):
            return True
    return False


def parse_nps_holding(
    html: str, *, expected_code: str, stock_name: str
) -> dict | None:
    """FnGuide Snapshot HTML에서 국민연금공단 주주현황 한 행을 추출한다.

    우선주 URL이 보통주 페이지로 연결되는 사례가 있으므로 페이지 제목의
    실제 종목코드가 요청 코드와 다르면 결과를 버린다.
    """
    if _snapshot_ticker(html) != expected_code.upper():
        return None

    row_match = _NPS_ROW_RE.search(html)
    if not row_match:
        return None

    common_shares, ratio, changed_at = (
        _cell_text(value) for value in row_match.groups()
    )
    if not ratio:
        return None
    return {
        "종목코드": expected_code.upper(),
        "종목명": normalize_stock_name(stock_name),
        "보통주": common_shares,
        "지분율(%)": ratio,
        "최종변동일": changed_at,
    }


def parse_nps_share_events(
    html: str, *, expected_code: str, stock_name: str
) -> list[dict]:
    """FnGuide 지분분석에서 국민연금공단 보통주 변동내역을 추출한다."""
    if _snapshot_ticker(html) != expected_code.upper():
        return []

    body_match = _SHARE_BODY_RE.search(html or "")
    if not body_match:
        return []

    events = []
    for row_html in _HTML_ROW_RE.findall(body_match.group(1)):
        cells = [_cell_text(cell) for cell in _HTML_CELL_RE.findall(row_html)]
        if len(cells) < 10 or cells[1] != "국민연금공단" or cells[5] != "보통주":
            continue
        try:
            before = int(cells[6].replace(",", ""))
            change = int(cells[7].replace(",", ""))
            after = int(cells[8].replace(",", ""))
            ratio = float(cells[9].replace(",", ""))
        except ValueError:
            continue
        events.append(
            {
                "종목코드": expected_code.upper(),
                "종목명": normalize_stock_name(stock_name),
                "변동일": cells[3].replace(".", "-").replace("/", "-"),
                "변동사유": cells[4],
                "주식종류": cells[5],
                "변동전": before,
                "증감": change,
                "변동후": after,
                "지분율(%)": ratio,
            }
        )
    return events


def _fetch_nps_one(
    stock_name: str,
    code: str,
    *,
    timeout: float,
    session_getter: Callable[[], requests.Session],
) -> tuple[bool, dict | None]:
    session = session_getter()
    response = session.get(
        SNAPSHOT_URL,
        params={"cmp_cd": code},
        headers=REQUEST_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    html = response.text
    page_matches = (
        _snapshot_ticker(html) == str(code).upper()
        and _has_snapshot_shareholder_table(html)
    )
    if not page_matches:
        return False, None
    return True, parse_nps_holding(html, expected_code=str(code), stock_name=stock_name)


def _fetch_nps_share_one(
    stock_name: str,
    code: str,
    *,
    timeout: float,
    session_getter: Callable[[], requests.Session],
) -> tuple[bool, list[dict]]:
    session = session_getter()
    response = session.get(
        SHARE_ANALYSIS_URL,
        params={"cmp_cd": code},
        headers=REQUEST_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    html = response.text
    page_matches = (
        _snapshot_ticker(html) == str(code).upper()
        and _has_share_change_table(html)
    )
    if not page_matches:
        return False, []
    return True, parse_nps_share_events(
        html, expected_code=str(code), stock_name=stock_name
    )


def fetch_nps_share_events(
    holdings: list[dict],
    *,
    require_coverage: bool,
    max_workers: int = 12,
    timeout: float = 15,
    verified_codes: set[str] | None = None,
) -> list[dict]:
    """현재 국민연금 보유 종목의 최근 주요주주 변동내역을 병렬 수집한다."""
    if not holdings:
        return []

    workers = max(1, min(int(max_workers), 32))
    rows = []
    failures = 0
    valid_pages = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _fetch_nps_share_one,
                normalize_stock_name(holding.get("종목명")),
                str(holding.get("종목코드") or ""),
                timeout=timeout,
                session_getter=_worker_session,
            ): holding
            for holding in holdings
        }
        for future in as_completed(futures):
            holding = futures[future]
            try:
                page_matches, page_rows = future.result()
                if page_matches:
                    valid_pages += 1
                    if verified_codes is not None:
                        verified_codes.add(
                            str(holding.get("종목코드") or "").strip().upper()
                        )
                    rows.extend(page_rows)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                logger.debug(
                    "국민연금 변동내역 조회 실패 (%s): %s",
                    holding.get("종목명"),
                    exc,
                )

    minimum_valid_pages = math.ceil(len(holdings) * 0.8)
    if valid_pages < minimum_valid_pages:
        message = (
            "국민연금 ShareAnalysis 유효 페이지 비율이 낮습니다 "
            f"({valid_pages}/{len(holdings)}, 최소 {minimum_valid_pages})"
        )
        if require_coverage:
            raise ScreeningDataError(message)
        logger.warning(message)

    if failures:
        logger.warning(
            "국민연금 ShareAnalysis 조회 실패: %d/%d", failures, len(holdings)
        )
    rows.sort(key=lambda row: (row.get("종목코드", ""), row.get("변동일", "")))
    logger.info(
        "국민연금 변동내역: %d건 (ShareAnalysis 유효 %d/%d)",
        len(rows),
        valid_pages,
        len(holdings),
    )
    return rows


def fetch_nps_holdings(
    ticker_map_path: str = DEFAULT_TICKER_MAP,
    *,
    max_workers: int = 12,
    timeout: float = 15,
    required_codes: set[str] | None = None,
) -> list[dict]:
    """전 종목 Snapshot에서 국민연금공단 5% 공시 보유 종목을 수집한다.

    직전 상태에 있던 ``required_codes``는 페이지를 검증하지 못하면 보유 해제로
    간주할 수 없으므로 전체 수집을 실패시켜 기존 상태를 보존한다.
    """
    try:
        with open(ticker_map_path, encoding="utf-8") as file:
            ticker_map = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ScreeningDataError(f"종목 코드 맵을 읽을 수 없습니다: {exc}") from exc

    if not isinstance(ticker_map, dict) or not ticker_map:
        raise ScreeningDataError("종목 코드 맵이 비어 있거나 올바르지 않습니다")

    required = {
        str(code).strip().upper()
        for code in (required_codes or set())
        if str(code).strip()
    }
    mapped_codes = {
        str(code).strip().upper()
        for code in ticker_map.values()
        if str(code).strip()
    }
    unverified_required = required - mapped_codes

    workers = max(1, min(int(max_workers), 32))
    rows = []
    failures = 0
    valid_pages = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _fetch_nps_one,
                normalize_stock_name(name),
                str(code),
                timeout=timeout,
                session_getter=_worker_session,
            ): (name, str(code).strip().upper())
            for name, code in ticker_map.items()
        }
        for future in as_completed(futures):
            name, code = futures[future]
            try:
                page_matches, row = future.result()
                valid_pages += int(page_matches)
                if not page_matches and code in required:
                    unverified_required.add(code)
                if row:
                    rows.append(row)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                if code in required:
                    unverified_required.add(code)
                logger.debug("국민연금 조회 실패 (%s): %s", name, exc)

    if unverified_required:
        sample = ", ".join(sorted(unverified_required)[:5])
        raise ScreeningDataError(
            "기존 보유 종목의 Snapshot을 검증하지 못했습니다 "
            f"({len(unverified_required)}개: {sample})"
        )

    minimum_valid_pages = max(1, math.ceil(len(ticker_map) * 0.8))
    if valid_pages < minimum_valid_pages:
        raise ScreeningDataError(
            "국민연금 Snapshot 유효 페이지 비율이 낮습니다 "
            f"({valid_pages}/{len(ticker_map)}, 최소 {minimum_valid_pages})"
        )

    rows.sort(key=lambda row: row["종목명"])
    for index, row in enumerate(rows, start=1):
        row["No."] = str(index)

    if failures:
        logger.warning("국민연금 Snapshot 조회 실패: %d/%d", failures, len(ticker_map))
    logger.info(
        "국민연금: %d개 종목 (Snapshot 유효 %d/%d)",
        len(rows),
        valid_pages,
        len(ticker_map),
    )
    return rows


def build_nps_buy_signals(
    ticker_map_path: str = DEFAULT_TICKER_MAP,
    state_path: str = DEFAULT_NPS_STATE,
    *,
    as_of: date | None = None,
) -> tuple[list[dict], dict]:
    """현재 보유·변동내역·직전 상태를 활성 국민연금 매수 신호로 병합한다."""
    effective_date = as_of or kst_today()
    previous_state = load_nps_state(state_path)
    previous_holdings = (previous_state or {}).get("holdings", {})
    required_codes = set(previous_holdings) if isinstance(previous_holdings, dict) else set()
    holdings = fetch_nps_holdings(
        ticker_map_path,
        required_codes=required_codes,
    )
    verified_codes: set[str] = set()
    events = fetch_nps_share_events(
        holdings,
        require_coverage=previous_state is None,
        verified_codes=verified_codes,
    )
    return reconcile_nps_signals(
        holdings,
        events,
        previous_state,
        as_of=effective_date,
        snapshot_inference_codes=verified_codes,
    )


def fetch_all_data(
    ticker_map_path: str = DEFAULT_TICKER_MAP,
    *,
    require_all: bool = False,
    nps_state_path: str = DEFAULT_NPS_STATE,
    as_of: date | None = None,
) -> tuple[list[dict], list[dict], list[dict]]:
    """세 소스를 독립적으로 수집하고 가능한 결과를 모두 반환한다.

    ``require_all``은 정상 응답의 빈 목록은 허용하되, 어느 한 소스라도
    요청 또는 응답 검증에 실패하면 전체 호출을 실패시킨다.
    """
    collected: list[list[dict]] = [[], [], []]
    errors = []
    pending_nps_state = None
    sources = ((0, "턴어라운드", fetch_turnaround), (1, "순매수전환", fetch_supply_trend))
    for index, label, fetcher in sources:
        try:
            collected[index] = fetcher()
        except Exception as exc:  # noqa: BLE001
            logger.error("%s 데이터 수집 실패: %s", label, exc)
            errors.append(f"{label}: {exc}")

    try:
        with nps_state_lock(nps_state_path):
            try:
                collected[2], pending_nps_state = build_nps_buy_signals(
                    ticker_map_path,
                    nps_state_path,
                    as_of=as_of,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("국민연금 데이터 수집 실패: %s", exc)
                errors.append(f"국민연금: {exc}")

            if errors and require_all:
                detail = "; ".join(errors)
                raise ScreeningDataError(f"필수 데이터 소스 수집 실패 ({detail})")
            if not any(collected) and errors:
                detail = "; ".join(errors) or "응답 데이터 없음"
                raise ScreeningDataError(f"모든 데이터 소스에서 수집 실패 ({detail})")
            if not errors and pending_nps_state is not None:
                try:
                    save_nps_state(nps_state_path, pending_nps_state)
                except Exception as exc:
                    raise ScreeningDataError(
                        f"국민연금 상태 저장 실패: {exc}"
                    ) from exc
            return collected[0], collected[1], collected[2]
    except NpsStateLockError as exc:
        logger.error("국민연금 상태 잠금 실패: %s", exc)
        errors.append(f"국민연금: {exc}")

    if errors and require_all:
        detail = "; ".join(errors)
        raise ScreeningDataError(f"필수 데이터 소스 수집 실패 ({detail})")
    if not any(collected):
        detail = "; ".join(errors) or "응답 데이터 없음"
        raise ScreeningDataError(f"모든 데이터 소스에서 수집 실패 ({detail})")
    return collected[0], collected[1], collected[2]


def calculate_scores(
    turn_data: list[dict], supply_data: list[dict], nps_data: list[dict]
) -> tuple[list[dict], dict]:
    """세 데이터셋의 포함 여부를 1점씩 합산하고 상세 값을 병합한다."""
    turn_map = {
        normalize_stock_name(row.get("종목명")): row
        for row in turn_data
        if normalize_stock_name(row.get("종목명"))
    }
    supply_map = {
        normalize_stock_name(row.get("종목명")): row
        for row in supply_data
        if normalize_stock_name(row.get("종목명"))
    }
    nps_map = {
        normalize_stock_name(row.get("종목명")): row
        for row in nps_data
        if normalize_stock_name(row.get("종목명"))
    }

    all_stocks = set(turn_map) | set(supply_map) | set(nps_map)
    results = []
    for stock in all_stocks:
        sources = []
        if stock in turn_map:
            sources.append("연간실적호전")
        if stock in supply_map:
            sources.append("순매수전환")
        if stock in nps_map:
            sources.append(NPS_SOURCE_LABEL)

        detail = {
            "종목명": stock,
            "종합점수": len(sources),
            "출처": ", ".join(sources),
        }
        for prefix, source_map in (
            ("턴", turn_map),
            ("수급", supply_map),
            ("연금", nps_map),
        ):
            if stock not in source_map:
                continue
            for key, value in source_map[stock].items():
                if key not in ("No.", "종목명"):
                    detail[f"[{prefix}]{key}"] = value
        results.append(detail)

    results.sort(key=lambda row: (-row["종합점수"], row["종목명"]))
    for index, row in enumerate(results, start=1):
        row["순위"] = index

    stats = {
        "turn_count": len(turn_map),
        "supply_count": len(supply_map),
        "nps_count": len(nps_map),
        "total": len(all_stocks),
        "score_3": sum(row["종합점수"] == 3 for row in results),
        "score_2": sum(row["종합점수"] == 2 for row in results),
        "score_1": sum(row["종합점수"] == 1 for row in results),
    }
    return results, stats
