#!/usr/bin/env python3
"""
DuckDB 기반 주가 데이터 스토리지
================================
- 일봉 데이터를 DuckDB에 저장/조회
- 증분 수집: 이미 저장된 날짜는 스킵, 새로운 날짜만 pykrx에서 가져옴
- 종목 매핑 캐시 (KRX 종목코드 ↔ 종목명)
- KOSPI 지수 데이터 관리

사용 예시:
    from stock_db import StockDB
    db = StockDB('stock_data.duckdb')
    db.ensure_price_data(['005930', '000660'], '20250101', '20250615')
    prices = db.get_prices('005930', '2025-01-01', '2025-06-15')
"""

import os
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional

import duckdb

logger = logging.getLogger(__name__)


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
            con.execute("""
                CREATE TABLE IF NOT EXISTS index_prices (
                    index_code VARCHAR NOT NULL,
                    date DATE NOT NULL,
                    close DOUBLE,
                    PRIMARY KEY (index_code, date)
                )
            """)
            # Indexes for faster reads
            con.execute("CREATE INDEX IF NOT EXISTS idx_daily_ticker ON daily_prices(ticker)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_prices(date)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_daily_ticker_date ON daily_prices(ticker, date)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_index_code ON index_prices(index_code)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_index_code_date ON index_prices(index_code, date)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_ticker_map_name ON ticker_map(name)")
        finally:
            con.close()
        logger.info(f"DuckDB 초기화 완료: {self.db_path}")

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

    def refresh_ticker_map(self, krx_module) -> Tuple[Dict[str, str], Dict[str, str]]:
        """
        pykrx로 KRX 전 종목 매핑을 갱신하고 DB에 저장

        Args:
            krx_module: pykrx.stock 모듈
        Returns:
            (name_to_code, code_to_name)
        """
        today = datetime.now().strftime('%Y%m%d')
        name_to_code = {}
        code_to_name = {}
        rows = []

        for market in ['KOSPI', 'KOSDAQ']:
            try:
                tickers = krx_module.get_market_ticker_list(today, market=market)
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

    def get_or_refresh_ticker_map(self, krx_module=None) -> Tuple[Dict[str, str], Dict[str, str]]:
        """
        DB에 캐시된 매핑이 있으면 사용, 없거나 오래되면 갱신
        """
        con = self._connect()
        try:
            result = con.execute("""
                SELECT COUNT(*), MIN(updated_at) FROM ticker_map
            """).fetchone()
            count = result[0]
            oldest = result[1]
        finally:
            con.close()

        # 매핑이 없거나 7일 이상 지난 경우 갱신
        need_refresh = count == 0
        if oldest and not need_refresh:
            if isinstance(oldest, str):
                oldest = datetime.fromisoformat(oldest)
            if (datetime.now() - oldest).days > 7:
                need_refresh = True

        if need_refresh and krx_module:
            logger.info("종목 매핑 갱신 중...")
            return self.refresh_ticker_map(krx_module)

        return self.get_ticker_map_from_db()

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

    def fetch_and_store(self, ticker: str, start_yyyymmdd: str, end_yyyymmdd: str,
                        krx_module=None) -> int:
        """
        증분 수집: DB에 없는 기간만 pykrx에서 가져와 저장

        Returns:
            새로 수집한 일수
        """
        if not krx_module:
            return 0

        # DB에 이미 있는 날짜 확인
        stored_dates = self.get_stored_dates(ticker)

        # pykrx 요청 기간 결정
        # 시작일과 끝일 형식 통일 (YYYYMMDD)
        start_s = start_yyyymmdd.replace('-', '')
        end_s = end_yyyymmdd.replace('-', '')

        # DB에 데이터가 있으면 마지막 날짜+1 부터만 수집
        if stored_dates:
            db_max = max(stored_dates)  # 'YYYY-MM-DD'
            db_max_yyyymmdd = db_max.replace('-', '')
            # 요청 끝날짜가 DB 최신보다 뒤면 그 부분만 수집
            if end_s <= db_max_yyyymmdd:
                # 요청 범위가 이미 모두 DB에 있을 가능성 높음
                # 시작 부분도 체크
                db_min = min(stored_dates)
                db_min_yyyymmdd = db_min.replace('-', '')
                if start_s >= db_min_yyyymmdd:
                    logger.debug(f"  {ticker}: DB에 충분한 데이터 존재")
                    return 0

        try:
            df = krx_module.get_market_ohlcv_by_date(start_s, end_s, ticker)
        except Exception as e:
            logger.warning(f"pykrx 데이터 조회 실패 ({ticker}): {e}")
            return 0

        new_data = []
        for date_idx, row in df.iterrows():
            d = date_idx.strftime('%Y-%m-%d') if hasattr(date_idx, 'strftime') else str(date_idx)[:10]
            if d in stored_dates:
                continue
            close_val = float(row.get('종가', 0))
            if close_val <= 0:
                continue
            new_data.append({
                'date': d,
                'open': float(row.get('시가', 0)),
                'high': float(row.get('고가', 0)),
                'low': float(row.get('저가', 0)),
                'close': close_val,
                'volume': int(row.get('거래량', 0)),
            })

        if new_data:
            self.save_prices(ticker, new_data)
            logger.info(f"  {ticker}: {len(new_data)}일 신규 수집 (기존 {len(stored_dates)}일)")

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

    def ensure_index_data(self, index_code: str, start_yyyymmdd: str, end_yyyymmdd: str,
                          krx_module=None) -> int:
        """지수 데이터 증분 수집"""
        if not krx_module:
            return 0

        # 기존 날짜
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

        try:
            df = krx_module.get_index_ohlcv_by_date(start_s, end_s, index_code)
        except Exception as e:
            logger.warning(f"지수 데이터 조회 실패 ({index_code}): {e}")
            return 0

        new_data = []
        for date_idx, row in df.iterrows():
            d = date_idx.strftime('%Y-%m-%d') if hasattr(date_idx, 'strftime') else str(date_idx)[:10]
            if d in stored:
                continue
            new_data.append({'date': d, 'close': float(row.get('종가', 0))})

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

    _ALLOWED_TABLES = {'daily_prices', 'ticker_map', 'index_prices'}

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
            table_name: One of daily_prices, ticker_map, index_prices
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
