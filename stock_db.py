#!/usr/bin/env python3
"""
DuckDB 기반 주가 데이터 스토리지
================================
- 일봉 데이터를 DuckDB에 저장/조회
- 증분 수집: 이미 저장된 날짜는 스킵, 새로운 날짜만 pykrx에서 가져옴
- 종목 매핑 캐시 (KRX 종목코드 ↔ 종목명)
- KOSPI 지수 데이터 관리
- 날짜별 종합 스크리닝 결과 저장

사용 예시:
    from stock_db import StockDB
    db = StockDB('stock_data.duckdb')
    db.ensure_price_data(['005930', '000660'], '20250101', '20250615')
    prices = db.get_prices('005930', '2025-01-01', '2025-06-15')
"""

import json
import logging
import math
import os
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple, Optional
from zoneinfo import ZoneInfo

import duckdb
import yfinance as yf

logger = logging.getLogger(__name__)
DEFAULT_TICKER_MAP_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'ticker_map.json'
)


class StockDB:
    """DuckDB 기반 주가 데이터 관리"""

    def __init__(self, db_path: str = None):
        if db_path is None:
            db_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                'stock_data.duckdb'
            )
        self.db_path = db_path
        self._init_tables()

    def _connect(self):
        """DuckDB 연결 (매 호출마다 새 연결 - 스레드 안전)"""
        return duckdb.connect(self.db_path)

    @staticmethod
    def _sync_daily_price_names(con) -> None:
        """ticker_map을 기준으로 저장된 일봉 종목명을 동기화한다."""
        con.execute("""
            UPDATE daily_prices AS dp
            SET name = tm.name
            FROM ticker_map AS tm
            WHERE dp.ticker = tm.ticker
              AND dp.name IS DISTINCT FROM tm.name
        """)

    def _init_tables(self):
        """테이블 초기화"""
        con = self._connect()
        try:
            con.execute("""
                CREATE TABLE IF NOT EXISTS daily_prices (
                    ticker VARCHAR NOT NULL,
                    date DATE NOT NULL,
                    open DOUBLE,
                    high DOUBLE,
                    low DOUBLE,
                    close DOUBLE,
                    volume BIGINT,
                    name VARCHAR,
                    PRIMARY KEY (ticker, date)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS ticker_map (
                    ticker VARCHAR PRIMARY KEY,
                    name VARCHAR NOT NULL,
                    market VARCHAR,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            con.execute(
                "ALTER TABLE daily_prices "
                "ADD COLUMN IF NOT EXISTS name VARCHAR"
            )
            self._sync_daily_price_names(con)
            con.execute("""
                CREATE TABLE IF NOT EXISTS index_prices (
                    index_code VARCHAR NOT NULL,
                    date DATE NOT NULL,
                    close DOUBLE,
                    PRIMARY KEY (index_code, date)
                )
            """)
            con.execute("""
                CREATE TABLE IF NOT EXISTS screening_results (
                    snapshot_date DATE NOT NULL,
                    stock_name VARCHAR NOT NULL,
                    score INTEGER NOT NULL,
                    matched_items VARCHAR NOT NULL,
                    details JSON NOT NULL,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (snapshot_date, stock_name)
                )
            """)
            # Indexes for faster reads
            con.execute("CREATE INDEX IF NOT EXISTS idx_daily_ticker ON daily_prices(ticker)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_prices(date)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_daily_ticker_date ON daily_prices(ticker, date)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_index_code ON index_prices(index_code)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_index_code_date ON index_prices(index_code, date)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_ticker_map_name ON ticker_map(name)")
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_screening_date "
                "ON screening_results(snapshot_date)"
            )
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_screening_score "
                "ON screening_results(score)"
            )
        finally:
            con.close()
        logger.info(f"DuckDB 초기화 완료: {self.db_path}")

    # ----------------------------------------------------------
    # 종합 스크리닝 결과
    # ----------------------------------------------------------
    def replace_screening_results(
        self,
        results: List[dict],
        snapshot_date: Optional[date] = None,
    ) -> int:
        """KST 날짜의 종합결과 전체를 원자적으로 교체한다."""
        snapshot_date = snapshot_date or datetime.now(
            ZoneInfo("Asia/Seoul")
        ).date()
        rows = []
        seen_names = set()

        for result in results:
            if not isinstance(result, dict):
                raise ValueError("종합결과 행은 dict여야 합니다")

            stock_name = str(result.get("종목명") or "").strip()
            if not stock_name:
                raise ValueError("종합결과에 종목명이 없습니다")
            if stock_name in seen_names:
                raise ValueError(f"종합결과에 중복 종목이 있습니다: {stock_name}")
            seen_names.add(stock_name)

            try:
                score = int(result["종합점수"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"종합결과 점수가 올바르지 않습니다: {stock_name}"
                ) from exc

            matched_items = str(result.get("출처") or "")
            details = {
                key: value
                for key, value in result.items()
                if key not in {"종목명", "종합점수", "출처", "순위"}
            }
            try:
                details_json = json.dumps(
                    details,
                    ensure_ascii=False,
                    allow_nan=False,
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"종합결과 상세정보를 저장할 수 없습니다: {stock_name}"
                ) from exc

            rows.append(
                (snapshot_date, stock_name, score, matched_items, details_json)
            )

        con = self._connect()
        transaction_started = False
        try:
            con.execute("BEGIN TRANSACTION")
            transaction_started = True
            con.execute(
                "DELETE FROM screening_results WHERE snapshot_date = ?",
                [snapshot_date],
            )
            if rows:
                con.executemany("""
                    INSERT INTO screening_results
                        (snapshot_date, stock_name, score, matched_items, details)
                    VALUES (?, ?, ?, ?, ?)
                """, rows)
            con.execute("COMMIT")
            transaction_started = False
        except Exception:
            if transaction_started:
                con.execute("ROLLBACK")
            raise
        finally:
            con.close()

        logger.info(
            "종합결과 저장: %s %d개",
            snapshot_date.isoformat(),
            len(rows),
        )
        return len(rows)

    # ----------------------------------------------------------
    # 종목 매핑
    # ----------------------------------------------------------
    def get_ticker_map_from_db(self) -> Tuple[Dict[str, str], Dict[str, str]]:
        """DB에서 종목 매핑 조회 → (name_to_code, code_to_name)"""
        con = self._connect()
        try:
            rows = con.execute("SELECT ticker, name FROM ticker_map").fetchall()
            name_to_code = {name: code for code, name in rows}
            code_to_name = {code: name for code, name in rows}
            return name_to_code, code_to_name
        finally:
            con.close()

    def load_ticker_map_file(
        self, path: str = DEFAULT_TICKER_MAP_PATH
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """저장소의 종목 매핑 JSON을 DuckDB 초기값으로 적재한다."""
        try:
            with open(path, encoding='utf-8') as file:
                raw_map = json.load(file)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"종목 매핑 파일 로드 실패 ({path}): {exc}")
            return {}, {}

        if not isinstance(raw_map, dict):
            logger.warning(f"종목 매핑 파일 구조가 올바르지 않습니다: {path}")
            return {}, {}

        name_to_code = {
            str(name).strip(): str(code).strip()
            for name, code in raw_map.items()
            if str(name).strip() and str(code).strip()
        }
        if not name_to_code:
            return {}, {}

        now_str = datetime.now().isoformat()
        rows = [
            (code, name, None, now_str)
            for name, code in name_to_code.items()
        ]
        con = self._connect()
        try:
            con.executemany("""
                INSERT INTO ticker_map (ticker, name, market, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (ticker) DO UPDATE SET
                    name = EXCLUDED.name,
                    updated_at = EXCLUDED.updated_at
            """, rows)
        finally:
            con.close()

        logger.info(f"종목 매핑 파일 적재: {len(rows)}개")
        return name_to_code, {
            code: name for name, code in name_to_code.items()
        }

    def refresh_ticker_map(self, krx_module) -> Tuple[Dict[str, str], Dict[str, str]]:
        """
        pykrx로 KRX 전 종목 매핑을 갱신하고 DB에 저장

        Args:
            krx_module: pykrx.stock 모듈
        Returns:
            (name_to_code, code_to_name)
        """
        # KRX API는 장 개장 전이나 휴일에 당일 데이터가 없으므로
        # 최근 7일 내 유효한 거래일을 찾아서 사용
        name_to_code = {}
        code_to_name = {}
        rows = []

        query_date = None
        for days_back in range(0, 8):
            candidate = (datetime.now() - timedelta(days=days_back)).strftime('%Y%m%d')
            try:
                test_tickers = krx_module.get_market_ticker_list(candidate, market='KOSPI')
                if test_tickers:
                    query_date = candidate
                    logger.info(f"KRX 유효 거래일: {candidate} (오늘-{days_back}일)")
                    break
            except Exception:
                continue

        if not query_date:
            logger.warning("최근 7일 내 유효한 KRX 거래일을 찾을 수 없음")
            return name_to_code, code_to_name

        for market in ['KOSPI', 'KOSDAQ']:
            try:
                tickers = krx_module.get_market_ticker_list(query_date, market=market)
                for code in tickers:
                    name = krx_module.get_market_ticker_name(code)
                    name_to_code[name] = code
                    code_to_name[code] = name
                    now_str = datetime.now().isoformat()
                    rows.append((code, name, market, now_str))
            except Exception as e:
                logger.warning(f"KRX {market} 종목 목록 조회 실패: {e}")

        if rows:
            con = self._connect()
            try:
                # UPSERT
                con.executemany("""
                    INSERT INTO ticker_map (ticker, name, market, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT (ticker) DO UPDATE SET
                        name = EXCLUDED.name,
                        market = EXCLUDED.market,
                        updated_at = EXCLUDED.updated_at
                """, rows)
                logger.info(f"종목 매핑 갱신: {len(rows)}개")
            finally:
                con.close()

        return name_to_code, code_to_name

    def get_or_refresh_ticker_map(
        self,
        krx_module=None,
        fallback_path: str = DEFAULT_TICKER_MAP_PATH,
    ) -> Tuple[Dict[str, str], Dict[str, str]]:
        """
        DB에 캐시된 매핑이 있으면 사용, 없거나 오래되면 갱신
        """
        con = self._connect()
        try:
            result = con.execute("""
                SELECT COUNT(*), MAX(updated_at) FROM ticker_map
            """).fetchone()
            count = result[0]
            newest = result[1]
        finally:
            con.close()

        # 매핑이 없거나 7일 이상 지난 경우 갱신
        need_refresh = count == 0
        if newest and not need_refresh:
            if isinstance(newest, str):
                newest = datetime.fromisoformat(newest)
            if (datetime.now() - newest).days > 7:
                need_refresh = True

        if need_refresh and krx_module:
            logger.info("종목 매핑 갱신 중...")
            refreshed = self.refresh_ticker_map(krx_module)
            if refreshed[0]:
                return refreshed
            logger.warning("KRX 종목 매핑 갱신 실패 - 기존 캐시를 유지합니다")

        cached = self.get_ticker_map_from_db()
        if cached[0]:
            return cached
        if fallback_path:
            return self.load_ticker_map_file(fallback_path)
        return {}, {}

    # ----------------------------------------------------------
    # 일봉 데이터
    # ----------------------------------------------------------
    def get_stored_date_range(self, ticker: str) -> Tuple[Optional[str], Optional[str]]:
        """DB에 저장된 해당 종목의 날짜 범위"""
        con = self._connect()
        try:
            row = con.execute("""
                SELECT MIN(date), MAX(date)
                FROM daily_prices WHERE ticker = ?
            """, [ticker]).fetchone()
            if row and row[0]:
                return str(row[0]), str(row[1])
            return None, None
        finally:
            con.close()

    def get_stored_dates(self, ticker: str) -> set:
        """DB에 저장된 해당 종목의 전체 날짜 집합"""
        con = self._connect()
        try:
            rows = con.execute("""
                SELECT CAST(date AS VARCHAR) FROM daily_prices WHERE ticker = ?
            """, [ticker]).fetchall()
            return {r[0] for r in rows}
        finally:
            con.close()

    def save_prices(self, ticker: str, data: List[dict]):
        """
        일봉 데이터 저장 (UPSERT)

        Args:
            ticker: 종목코드
            data: [{'date': 'YYYY-MM-DD', 'open': .., 'high': .., 'low': .., 'close': .., 'volume': ..}]
        """
        if not data:
            return

        con = self._connect()
        try:
            rows = [
                (ticker, d['date'], d['open'], d['high'], d['low'], d['close'], d['volume'])
                for d in data
            ]
            con.executemany("""
                INSERT INTO daily_prices (ticker, date, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker, date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    volume = EXCLUDED.volume
            """, rows)
            logger.debug(f"  {ticker}: {len(rows)}일 저장")
        finally:
            con.close()

    def get_prices(self, ticker: str, start_date: str, end_date: str) -> List[dict]:
        """
        DB에서 일봉 데이터 조회

        Args:
            start_date, end_date: 'YYYY-MM-DD' 형식
        Returns:
            [{'date': .., 'open': .., 'high': .., 'low': .., 'close': .., 'volume': ..}]
        """
        con = self._connect()
        try:
            rows = con.execute("""
                SELECT CAST(date AS VARCHAR), open, high, low, close, volume
                FROM daily_prices
                WHERE ticker = ? AND date >= ? AND date <= ?
                ORDER BY date
            """, [ticker, start_date, end_date]).fetchall()

            return [
                {'date': r[0], 'open': r[1], 'high': r[2],
                 'low': r[3], 'close': r[4], 'volume': int(r[5] or 0)}
                for r in rows
            ]
        finally:
            con.close()

    @staticmethod
    def _number(value, default: float = 0.0) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        return number if math.isfinite(number) else default

    @staticmethod
    def _yfinance_end_date(end_yyyymmdd: str) -> str:
        """yfinance의 배타적 end 인수에 맞춰 하루 뒤 날짜를 반환한다."""
        compact = end_yyyymmdd.replace('-', '')
        return (
            datetime.strptime(compact, '%Y%m%d') + timedelta(days=1)
        ).strftime('%Y-%m-%d')

    def _download_yfinance(
        self, symbol: str, start_yyyymmdd: str, end_yyyymmdd: str
    ):
        start = datetime.strptime(
            start_yyyymmdd.replace('-', ''), '%Y%m%d'
        ).strftime('%Y-%m-%d')
        frame = yf.download(
            symbol,
            start=start,
            end=self._yfinance_end_date(end_yyyymmdd),
            progress=False,
            auto_adjust=True,
            threads=False,
        )
        if getattr(frame.columns, 'nlevels', 1) > 1:
            frame = frame.copy()
            frame.columns = frame.columns.get_level_values(0)
        return frame

    def _fetch_yfinance_stock(
        self, ticker: str, start_yyyymmdd: str, end_yyyymmdd: str
    ) -> List[dict]:
        """KRX 인증 없이 yfinance의 KOSPI/KOSDAQ 심볼을 순서대로 조회한다."""
        for suffix in ('.KS', '.KQ'):
            symbol = f"{ticker}{suffix}"
            try:
                frame = self._download_yfinance(
                    symbol, start_yyyymmdd, end_yyyymmdd
                )
            except Exception as exc:
                logger.debug(f"yfinance 조회 실패 ({symbol}): {exc}")
                continue
            if frame.empty:
                continue

            rows = []
            for date_idx, row in frame.iterrows():
                close = self._number(row.get('Close'))
                if close <= 0:
                    continue
                rows.append({
                    'date': date_idx.strftime('%Y-%m-%d'),
                    'open': self._number(row.get('Open'), close),
                    'high': self._number(row.get('High'), close),
                    'low': self._number(row.get('Low'), close),
                    'close': close,
                    'volume': int(self._number(row.get('Volume'))),
                })
            if rows:
                logger.info(f"  {ticker}: yfinance {len(rows)}일 수집")
                return rows
        return []

    def _fetch_yfinance_index(
        self, index_code: str, start_yyyymmdd: str, end_yyyymmdd: str
    ) -> List[dict]:
        """지원 지수의 yfinance 대체 데이터를 반환한다."""
        symbol = {'1001': '^KS11'}.get(index_code)
        if not symbol:
            return []
        try:
            frame = self._download_yfinance(
                symbol, start_yyyymmdd, end_yyyymmdd
            )
        except Exception as exc:
            logger.warning(f"yfinance 지수 조회 실패 ({symbol}): {exc}")
            return []

        rows = []
        for date_idx, row in frame.iterrows():
            close = self._number(row.get('Close'))
            if close > 0:
                rows.append({
                    'date': date_idx.strftime('%Y-%m-%d'),
                    'close': close,
                })
        if rows:
            logger.info(f"  KOSPI 지수: yfinance {len(rows)}일 수집")
        return rows

    def fetch_and_store(
        self, ticker: str, start_yyyymmdd: str, end_yyyymmdd: str,
        krx_module=None
    ) -> int:
        """
        증분 수집: DB에 없는 기간만 pykrx/yfinance에서 가져와 저장

        Returns:
            새로 수집한 일수
        """
        stored_dates = self.get_stored_dates(ticker)
        start_s = start_yyyymmdd.replace('-', '')
        end_s = end_yyyymmdd.replace('-', '')

        if stored_dates:
            db_max = max(stored_dates)
            db_max_yyyymmdd = db_max.replace('-', '')
            if end_s <= db_max_yyyymmdd:
                db_min = min(stored_dates)
                db_min_yyyymmdd = db_min.replace('-', '')
                if start_s >= db_min_yyyymmdd:
                    logger.debug(f"  {ticker}: DB에 충분한 데이터 존재")
                    return 0

        new_data = []
        if krx_module:
            try:
                df = krx_module.get_market_ohlcv_by_date(
                    start_s, end_s, ticker
                )
            except Exception as exc:
                logger.warning(f"pykrx 데이터 조회 실패 ({ticker}): {exc}")
            else:
                for date_idx, row in df.iterrows():
                    d = (
                        date_idx.strftime('%Y-%m-%d')
                        if hasattr(date_idx, 'strftime')
                        else str(date_idx)[:10]
                    )
                    if d in stored_dates:
                        continue
                    close = self._number(row.get('종가'))
                    if close <= 0:
                        continue
                    new_data.append({
                        'date': d,
                        'open': self._number(row.get('시가'), close),
                        'high': self._number(row.get('고가'), close),
                        'low': self._number(row.get('저가'), close),
                        'close': close,
                        'volume': int(self._number(row.get('거래량'))),
                    })

        if not new_data:
            new_data = [
                row
                for row in self._fetch_yfinance_stock(
                    ticker, start_s, end_s
                )
                if row['date'] not in stored_dates
            ]

        if new_data:
            self.save_prices(ticker, new_data)
            logger.info(
                f"  {ticker}: {len(new_data)}일 신규 수집 "
                f"(기존 {len(stored_dates)}일)"
            )

        return len(new_data)

    def ensure_price_data(self, tickers: List[str], start_yyyymmdd: str, end_yyyymmdd: str,
                          krx_module=None, progress_callback=None, delay: float = 0.3) -> dict:
        """
        여러 종목의 데이터를 한번에 증분 수집

        Args:
            tickers: 종목코드 리스트
            start_yyyymmdd, end_yyyymmdd: 'YYYYMMDD' 형식
            krx_module: pykrx.stock 모듈
            progress_callback: fn(loaded, total, ticker_name) 콜백
            delay: API 호출 간 대기 시간(초)

        Returns:
            {'total': 전체 종목수, 'fetched': API 호출 종목수, 'new_days': 신규 일수}
        """
        stats = {'total': len(tickers), 'fetched': 0, 'new_days': 0}

        for i, ticker in enumerate(tickers):
            if progress_callback:
                progress_callback(i + 1, len(tickers), ticker)

            new = self.fetch_and_store(ticker, start_yyyymmdd, end_yyyymmdd, krx_module)
            if new > 0:
                stats['fetched'] += 1
                stats['new_days'] += new
                time.sleep(delay)  # API 속도 제한
            else:
                time.sleep(0.05)  # DB만 읽은 경우 짧게

        return stats

    # ----------------------------------------------------------
    # KOSPI 지수
    # ----------------------------------------------------------
    def save_index_prices(self, index_code: str, data: List[dict]):
        """지수 데이터 저장"""
        if not data:
            return
        con = self._connect()
        try:
            rows = [(index_code, d['date'], d['close']) for d in data]
            con.executemany("""
                INSERT INTO index_prices (index_code, date, close)
                VALUES (?, ?, ?)
                ON CONFLICT (index_code, date) DO UPDATE SET close = EXCLUDED.close
            """, rows)
        finally:
            con.close()

    def get_index_prices(self, index_code: str, start_date: str, end_date: str) -> List[dict]:
        """DB에서 지수 데이터 조회"""
        con = self._connect()
        try:
            rows = con.execute("""
                SELECT CAST(date AS VARCHAR), close
                FROM index_prices
                WHERE index_code = ? AND date >= ? AND date <= ?
                ORDER BY date
            """, [index_code, start_date, end_date]).fetchall()
            return [{'date': r[0], 'close': r[1]} for r in rows]
        finally:
            con.close()

    def ensure_index_data(
        self, index_code: str, start_yyyymmdd: str, end_yyyymmdd: str,
        krx_module=None
    ) -> int:
        """지수 데이터 증분 수집"""
        con = self._connect()
        try:
            rows = con.execute("""
                SELECT CAST(date AS VARCHAR) FROM index_prices
                WHERE index_code = ?
            """, [index_code]).fetchall()
            stored = {r[0] for r in rows}
        finally:
            con.close()

        start_s = start_yyyymmdd.replace('-', '')
        end_s = end_yyyymmdd.replace('-', '')

        if stored:
            stored_compact = {date.replace('-', '') for date in stored}
            if (
                start_s >= min(stored_compact)
                and end_s <= max(stored_compact)
            ):
                logger.debug("  KOSPI 지수: DB에 충분한 데이터 존재")
                return 0

        new_data = []
        if krx_module:
            try:
                df = krx_module.get_index_ohlcv_by_date(
                    start_s, end_s, index_code
                )
            except Exception as exc:
                logger.warning(
                    f"지수 데이터 조회 실패 ({index_code}): {exc}"
                )
            else:
                for date_idx, row in df.iterrows():
                    d = (
                        date_idx.strftime('%Y-%m-%d')
                        if hasattr(date_idx, 'strftime')
                        else str(date_idx)[:10]
                    )
                    close = self._number(row.get('종가'))
                    if d not in stored and close > 0:
                        new_data.append({'date': d, 'close': close})

        if not new_data:
            new_data = [
                row
                for row in self._fetch_yfinance_index(
                    index_code, start_s, end_s
                )
                if row['date'] not in stored
            ]

        if new_data:
            self.save_index_prices(index_code, new_data)
            logger.info(f"  KOSPI 지수: {len(new_data)}일 신규 수집")

        return len(new_data)

    # ----------------------------------------------------------
    # 통계/유틸
    # ----------------------------------------------------------
    def get_db_stats(self) -> dict:
        """DB 현황 통계"""
        con = self._connect()
        try:
            price_count = con.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
            ticker_count = con.execute("SELECT COUNT(DISTINCT ticker) FROM daily_prices").fetchone()[0]
            map_count = con.execute("SELECT COUNT(*) FROM ticker_map").fetchone()[0]
            index_count = con.execute("SELECT COUNT(*) FROM index_prices").fetchone()[0]

            date_range = con.execute("""
                SELECT CAST(MIN(date) AS VARCHAR), CAST(MAX(date) AS VARCHAR)
                FROM daily_prices
            """).fetchone()

            return {
                'total_records': price_count,
                'total_tickers': ticker_count,
                'ticker_map_count': map_count,
                'index_records': index_count,
                'date_min': date_range[0] if date_range else None,
                'date_max': date_range[1] if date_range else None,
                'db_size_mb': round(os.path.getsize(self.db_path) / 1024 / 1024, 2)
                    if os.path.exists(self.db_path) else 0,
            }
        finally:
            con.close()

    # ----------------------------------------------------------
    # DB 뷰어용 조회 메서드
    # ----------------------------------------------------------

    _ALLOWED_TABLES = {
        'daily_prices',
        'ticker_map',
        'index_prices',
        'screening_results',
    }

    def get_table_list(self) -> List[dict]:
        """Get list of all tables with row counts"""
        con = self._connect()
        try:
            result = []
            for table_name in sorted(self._ALLOWED_TABLES):
                count = con.execute(
                    f"SELECT COUNT(*) FROM {table_name}"
                ).fetchone()[0]
                result.append({'table_name': table_name, 'row_count': count})
            return result
        finally:
            con.close()

    def get_table_schema(self, table_name: str) -> List[dict]:
        """Get column info for a table"""
        if table_name not in self._ALLOWED_TABLES:
            raise ValueError(f"Unknown table: {table_name!r}")
        con = self._connect()
        try:
            rows = con.execute(f"PRAGMA table_info('{table_name}')").fetchall()
            # PRAGMA table_info columns: cid, name, type, notnull, dflt_value, pk
            return [{'column_name': r[1], 'column_type': r[2]} for r in rows]
        finally:
            con.close()

    def query_table(self, table_name: str, page: int = 1, page_size: int = 50,
                    order_by: str = None, order_dir: str = 'DESC',
                    filter_col: str = None, filter_val: str = None) -> dict:
        """Paginated table query with optional filtering

        Args:
            table_name: One of the names in ``_ALLOWED_TABLES``
            page: 1-based page number
            page_size: rows per page
            order_by: column name to sort by (whitelisted against schema)
            order_dir: 'ASC' or 'DESC'
            filter_col: column to apply LIKE filter on (whitelisted against schema)
            filter_val: value for LIKE filter

        Returns:
            {'rows': [...], 'total': int, 'page': int, 'page_size': int, 'total_pages': int}
        """
        if table_name not in self._ALLOWED_TABLES:
            raise ValueError(f"Unknown table: {table_name!r}")

        # Whitelist order_dir
        order_dir = 'DESC' if order_dir not in ('ASC', 'DESC') else order_dir

        # Get valid column names for this table
        schema = self.get_table_schema(table_name)
        valid_cols = {col['column_name'] for col in schema}

        # Whitelist order_by and filter_col against actual schema
        if order_by and order_by not in valid_cols:
            order_by = None
        if filter_col and filter_col not in valid_cols:
            filter_col = None

        con = self._connect()
        try:
            # Build WHERE clause
            params: list = []
            where_clause = ""
            if filter_col and filter_val is not None:
                where_clause = f"WHERE CAST({filter_col} AS VARCHAR) LIKE ?"
                params.append(f"%{filter_val}%")

            # Total count
            total = con.execute(
                f"SELECT COUNT(*) FROM {table_name} {where_clause}", params
            ).fetchone()[0]

            # Build ORDER BY clause
            order_clause = ""
            if order_by:
                order_clause = f"ORDER BY {order_by} {order_dir}"

            # Pagination
            offset = (max(1, page) - 1) * page_size
            rows_raw = con.execute(
                f"SELECT * FROM {table_name} {where_clause} {order_clause} LIMIT ? OFFSET ?",
                params + [page_size, offset]
            ).fetchall()

            col_names = [col['column_name'] for col in schema]
            rows = [dict(zip(col_names, r)) for r in rows_raw]

            total_pages = max(1, (total + page_size - 1) // page_size)
            return {
                'rows': rows,
                'total': total,
                'page': page,
                'page_size': page_size,
                'total_pages': total_pages,
            }
        finally:
            con.close()

    def get_ticker_summary(self) -> List[dict]:
        """Get summary per ticker: ticker, name, min_date, max_date, count, latest_close"""
        con = self._connect()
        try:
            rows = con.execute("""
                SELECT
                    dp.ticker,
                    tm.name,
                    CAST(MIN(dp.date) AS VARCHAR) AS min_date,
                    CAST(MAX(dp.date) AS VARCHAR) AS max_date,
                    COUNT(*) AS count,
                    dp.close AS latest_close
                FROM daily_prices dp
                LEFT JOIN ticker_map tm ON dp.ticker = tm.ticker
                INNER JOIN (
                    SELECT ticker, MAX(date) AS max_d FROM daily_prices GROUP BY ticker
                ) latest ON dp.ticker = latest.ticker AND dp.date = latest.max_d
                GROUP BY dp.ticker, tm.name, dp.close
                ORDER BY dp.ticker
            """).fetchall()
            return [
                {
                    'ticker': r[0],
                    'name': r[1],
                    'min_date': r[2],
                    'max_date': r[3],
                    'count': r[4],
                    'latest_close': r[5],
                }
                for r in rows
            ]
        finally:
            con.close()
