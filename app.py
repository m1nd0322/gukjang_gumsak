#!/usr/bin/env python3
"""
한국 증시 종합 스크리닝 시스템 - 웹 서버 버전
- Flask 기반 웹 대시보드
- 재조회 버튼으로 실시간 데이터 갱신
- 매일 아침 8시 자동 갱신 (APScheduler)
- Selenium (headless Chrome) 기반 크롤링
- 백테스트 기능 (커스텀 엔진)

실행: python app.py
브라우저: http://localhost:5000
"""

from flask import Flask, jsonify, render_template_string, request, Response
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
import json
import math
import os
import logging
import threading
import traceback

from backtester import BacktestEngine
from screening import calculate_scores, fetch_all_data
from stock_db import StockDB

# ============================================================
# 설정
# ============================================================
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

DATA_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(DATA_DIR, 'cache_data.json')
CACHE_VERSION = 2

# 글로벌 데이터 저장소
current_data = {
    'turn': [],
    'supply': [],
    'nps': [],
    'result': [],
    'stats': {},
    'last_updated': None,
    'status': 'idle',  # idle, loading, done, error
    'error_msg': '',
}
data_lock = threading.Lock()
refresh_lock = threading.Lock()

# 백테스트 상태
backtest_state = {
    'status': 'idle',  # idle, loading, done, error
    'results': None,
    'error_msg': '',
    'progress': '',
    'engine': None,  # BacktestEngine 객체 보관 (CSV용)
}
bt_lock = threading.Lock()

# KRX 인증 정보가 있으면 pykrx를 우선 사용하고, 없으면 StockDB의
# ticker_map.json/yfinance 경로를 사용한다. pykrx 1.2.8부터 KRX 데이터
# API는 로그인 환경 변수가 필요하다.
HAS_PYKRX = False
krx = None
if os.getenv('KRX_ID') and os.getenv('KRX_PW'):
    try:
        from pykrx import stock as krx
        HAS_PYKRX = True
    except ImportError:
        logger.warning("pykrx 미설치 - yfinance 대체 경로를 사용합니다")
else:
    logger.info("KRX 인증 정보 없음 - ticker_map.json/yfinance 경로를 사용합니다")

# DuckDB 스토리지
stock_db = StockDB()


# ============================================================
# 데이터 갱신
# ============================================================
def refresh_data():
    """한 번에 하나의 데이터 갱신만 실행한다."""
    if not refresh_lock.acquire(blocking=False):
        logger.info("이미 데이터 갱신이 진행 중이므로 중복 실행을 건너뜁니다")
        return False
    return _run_reserved_refresh()


def _run_reserved_refresh():
    """이미 확보한 단일 실행 잠금을 해제할 때까지 갱신한다."""
    try:
        return _refresh_data_locked()
    finally:
        refresh_lock.release()


def _refresh_data_locked():
    """데이터 수집 → 점수 계산 → 저장"""
    global current_data

    with data_lock:
        current_data['status'] = 'loading'
        current_data['error_msg'] = ''

    logger.info("=" * 50)
    logger.info("데이터 갱신 시작")

    try:
        # JSON 피드와 종목별 Snapshot에서 세 소스 수집
        turn, supply, nps = fetch_all_data(require_all=True)

        result, stats = calculate_scores(turn, supply, nps)
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with data_lock:
            current_data['turn'] = turn
            current_data['supply'] = supply
            current_data['nps'] = nps
            current_data['result'] = result
            current_data['stats'] = stats
            current_data['last_updated'] = now
            current_data['status'] = 'done'

        # 캐시 파일 저장
        cache = {
            'version': CACHE_VERSION,
            'turn': turn, 'supply': supply, 'nps': nps,
            'result': result, 'stats': stats, 'last_updated': now,
        }
        with open(CACHE_FILE, 'w', encoding='utf-8') as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)

        logger.info(f"데이터 갱신 완료: 3점={stats['score_3']}, 2점={stats['score_2']}, 1점={stats['score_1']}")
        return True

    except Exception as e:
        logger.error(f"데이터 갱신 실패: {e}")
        with data_lock:
            # 기존 캐시 데이터가 있으면 유지하고 상태만 error로 표시
            if current_data.get('last_updated'):
                current_data['status'] = 'done'  # 기존 데이터로 복원
                current_data['error_msg'] = f"갱신 실패 (이전 데이터 유지): {e}"
                logger.info("기존 캐시 데이터를 유지합니다.")
            else:
                current_data['status'] = 'error'
                current_data['error_msg'] = str(e)
        return False


def load_cache():
    """캐시 파일에서 데이터 로드"""
    global current_data
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r', encoding='utf-8') as f:
                cache = json.load(f)
            if cache.get('version') != CACHE_VERSION:
                logger.info("구형 스크리닝 캐시를 무시합니다")
                return False
            with data_lock:
                current_data['turn'] = cache.get('turn', [])
                current_data['supply'] = cache.get('supply', [])
                current_data['nps'] = cache.get('nps', [])
                current_data['result'] = cache.get('result', [])
                current_data['stats'] = cache.get('stats', {})
                current_data['last_updated'] = cache.get('last_updated')
                current_data['status'] = 'done'
            logger.info(f"캐시 데이터 로드 완료 (갱신: {current_data['last_updated']})")
            return True
        except Exception as e:
            logger.error(f"캐시 로드 실패: {e}")
    return False


# ============================================================
# Flask 라우트
# ============================================================
@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route('/api/refresh', methods=['POST'])
def api_refresh():
    """재조회 API - 비동기 데이터 갱신"""
    if not refresh_lock.acquire(blocking=False):
        return jsonify({'status': 'already_loading', 'message': '이미 갱신 중입니다.'})

    with data_lock:
        current_data['status'] = 'loading'
        current_data['error_msg'] = ''

    try:
        thread = threading.Thread(target=_run_reserved_refresh, daemon=True)
        thread.start()
    except Exception as e:
        refresh_lock.release()
        with data_lock:
            if current_data.get('last_updated'):
                current_data['status'] = 'done'
                current_data['error_msg'] = f"갱신 시작 실패 (이전 데이터 유지): {e}"
            else:
                current_data['status'] = 'error'
                current_data['error_msg'] = str(e)
        return jsonify({'error': '갱신 작업을 시작하지 못했습니다.'}), 500
    return jsonify({'status': 'started', 'message': '데이터 갱신을 시작합니다.'})


@app.route('/api/status')
def api_status():
    """현재 상태 및 데이터 반환"""
    with data_lock:
        return jsonify({
            'status': current_data['status'],
            'last_updated': current_data['last_updated'],
            'error_msg': current_data['error_msg'],
            'stats': current_data['stats'],
            'result': current_data['result'],
            'turn': current_data['turn'],
            'supply': current_data['supply'],
            'nps': current_data['nps'],
        })


# ============================================================
# 백테스트 - DuckDB 기반 데이터 수집 및 실행
# ============================================================
def run_backtest_task(period_months, initial_capital, strategy,
                      slippage_pct=0.3, commission_pct=0.015, tax_pct=0.20):
    """백테스트 실행 (별도 스레드) - DuckDB 증분 수집"""
    global backtest_state

    try:
        with bt_lock:
            backtest_state['status'] = 'loading'
            backtest_state['progress'] = '종목 코드 매핑 중...'
            backtest_state['error_msg'] = ''

        # 1. 2점 이상 종목 추출
        with data_lock:
            results = current_data.get('result', [])
        high_score = [r for r in results if r.get('종합점수', 0) >= 2]

        if not high_score:
            raise Exception("2점 이상 종목이 없습니다. 먼저 스크리닝을 실행하세요.")

        stock_names = [r['종목명'] for r in high_score]
        logger.info(f"백테스트 대상: {len(stock_names)}개 종목 ({', '.join(stock_names[:5])}...)")

        # 2. 종목코드 매핑 (DuckDB 캐시 + pykrx 갱신)
        with bt_lock:
            backtest_state['progress'] = f'종목 코드 매핑 중... ({len(stock_names)}종목)'

        krx_mod = krx if HAS_PYKRX else None
        name_to_code, code_to_name = stock_db.get_or_refresh_ticker_map(krx_mod)

        matched = {}
        unmatched = []
        for name in stock_names:
            code = name_to_code.get(name)
            if code:
                matched[code] = name
            else:
                unmatched.append(name)

        if not matched:
            raise Exception(f"종목코드 매핑 실패: {', '.join(stock_names[:5])}")

        if unmatched:
            logger.warning(f"코드 매핑 실패 종목: {', '.join(unmatched)}")

        logger.info(f"코드 매핑 완료: {len(matched)}개 성공, {len(unmatched)}개 실패")

        # 3. 기간 설정
        end_dt = datetime.now()
        start_dt = end_dt - timedelta(days=period_months * 30)
        start_str = start_dt.strftime('%Y%m%d')
        end_str = end_dt.strftime('%Y%m%d')
        start_iso = start_dt.strftime('%Y-%m-%d')
        end_iso = end_dt.strftime('%Y-%m-%d')

        # 4. DuckDB 증분 수집 (이미 있는 데이터는 스킵)
        ticker_list = list(matched.keys())

        def progress_cb(loaded, total, ticker):
            name = matched.get(ticker, ticker)
            with bt_lock:
                backtest_state['progress'] = f'주가 데이터 수집 중... ({loaded}/{total}) {name}'

        with bt_lock:
            backtest_state['progress'] = f'주가 데이터 증분 수집 중... (총 {len(ticker_list)}종목)'

        fetch_stats = stock_db.ensure_price_data(
            ticker_list, start_str, end_str,
            krx_module=krx_mod,
            progress_callback=progress_cb,
        )
        logger.info(f"데이터 수집: API 호출 {fetch_stats['fetched']}종목, "
                     f"신규 {fetch_stats['new_days']}일 (DB 캐시 활용)")

        # 5. DuckDB에서 데이터 로드 → 백테스트 엔진
        engine = BacktestEngine(
            initial_capital=initial_capital,
            slippage_pct=slippage_pct,
            commission_pct=commission_pct,
            tax_pct=tax_pct,
        )

        for code, name in matched.items():
            prices = stock_db.get_prices(code, start_iso, end_iso)
            if prices:
                engine.add_price_data(code, prices, name=name)
            else:
                logger.warning(f"  {name}({code}): DuckDB에 데이터 없음")

        if not engine.price_data:
            raise Exception("가격 데이터를 수집한 종목이 없습니다.")

        # 6. 벤치마크 (KOSPI) - DuckDB 증분 수집
        with bt_lock:
            backtest_state['progress'] = 'KOSPI 벤치마크 데이터 수집 중...'
        stock_db.ensure_index_data("1001", start_str, end_str, krx_module=krx_mod)
        kospi = stock_db.get_index_prices("1001", start_iso, end_iso)
        if kospi:
            engine.set_benchmark(kospi)

        # 7. 백테스트 실행
        with bt_lock:
            backtest_state['progress'] = '백테스트 실행 중...'

        tickers = list(engine.price_data.keys())
        if strategy == 'rebalance':
            engine.run_rebalance(tickers, period=20)
        elif strategy == 'vol_trailing_stop':
            engine.run_volatility_trailing_stop(
                tickers, lookback=20, stop_pct=-10.0,
                cooldown=5, reentry=True)
        elif strategy == 'ma_filter':
            engine.run_ma_filter(
                tickers, ma_period=20, rebalance_period=5)
        elif strategy == 'composite':
            engine.run_composite(
                tickers, ma_period=20, lookback=20,
                stop_pct=-8.0, cooldown=5, rebalance_period=10)
        else:
            engine.run_equal_weight(tickers)

        results = engine.get_results()

        # 8. 추가 정보
        db_stats = stock_db.get_db_stats()
        strategy_names = {
            'equal_weight': '동일 비중 Buy & Hold',
            'rebalance': '월간 리밸런싱 (20일)',
            'vol_trailing_stop': '변동성 가중 + 트레일링 스탑',
            'ma_filter': '이동평균 필터 (MA20)',
            'composite': '복합 전략 (MA + 변동성 + 스탑)',
        }
        results['config'] = {
            'period_months': period_months,
            'initial_capital': initial_capital,
            'strategy': strategy,
            'strategy_name': strategy_names.get(strategy, strategy),
            'total_stocks': len(matched),
            'loaded_stocks': len(engine.price_data),
            'unmatched': unmatched,
        }
        results['db_stats'] = db_stats

        with bt_lock:
            backtest_state['status'] = 'done'
            backtest_state['results'] = results
            backtest_state['progress'] = ''
            backtest_state['engine'] = engine

        logger.info(f"백테스트 완료: 수익률={results['metrics']['total_return']}%, "
                     f"MDD={results['metrics']['mdd']}%, DB크기={db_stats['db_size_mb']}MB")

    except Exception as e:
        logger.error(f"백테스트 실패: {e}\n{traceback.format_exc()}")
        with bt_lock:
            backtest_state['status'] = 'error'
            backtest_state['error_msg'] = str(e)
            backtest_state['progress'] = ''


# ============================================================
# 백테스트 Flask 라우트
# ============================================================
@app.route('/backtest')
def backtest_page():
    return render_template_string(BACKTEST_TEMPLATE)


@app.route('/api/backtest/run', methods=['POST'])
def api_backtest_run():
    with bt_lock:
        if backtest_state['status'] == 'loading':
            return jsonify({'status': 'already_loading', 'message': '이미 실행 중입니다.'})

    params = request.get_json(silent=True)
    if params is None:
        if request.get_data(cache=True):
            return jsonify({'error': '올바른 JSON 객체 형식이 필요합니다.'}), 400
        params = {}
    if not isinstance(params, dict):
        return jsonify({'error': 'JSON 객체 형식의 요청이 필요합니다.'}), 400
    try:
        period = int(params.get('period', 6))
        capital = int(params.get('capital', 100_000_000))
        slippage = float(params.get('slippage', 0.3))
        commission = float(params.get('commission', 0.015))
        tax = float(params.get('tax', 0.20))
    except (TypeError, ValueError):
        return jsonify({'error': '기간, 자본금, 거래비용은 숫자여야 합니다.'}), 400

    strategy = params.get('strategy', 'equal_weight')
    allowed_strategies = {
        'equal_weight', 'rebalance', 'vol_trailing_stop', 'ma_filter', 'composite'
    }
    if strategy not in allowed_strategies:
        return jsonify({'error': '지원하지 않는 백테스트 전략입니다.'}), 400
    if not 1 <= period <= 120:
        return jsonify({'error': '기간은 1~120개월이어야 합니다.'}), 400
    if capital <= 0:
        return jsonify({'error': '초기 자본금은 0보다 커야 합니다.'}), 400
    costs = (slippage, commission, tax)
    if not all(math.isfinite(value) and 0 <= value < 100 for value in costs):
        return jsonify({'error': '거래비용은 0 이상 100 미만이어야 합니다.'}), 400

    with bt_lock:
        if backtest_state['status'] == 'loading':
            return jsonify({'status': 'already_loading', 'message': '이미 실행 중입니다.'})
        backtest_state.update(
            status='loading',
            results=None,
            error_msg='',
            progress='백테스트 준비 중...',
            engine=None,
        )

    thread = threading.Thread(
        target=run_backtest_task,
        args=(period, capital, strategy, slippage, commission, tax),
        daemon=True,
    )
    try:
        thread.start()
    except Exception as e:
        with bt_lock:
            backtest_state['status'] = 'error'
            backtest_state['error_msg'] = str(e)
            backtest_state['progress'] = ''
        return jsonify({'error': '백테스트 작업을 시작하지 못했습니다.'}), 500
    return jsonify({'status': 'started', 'message': '백테스트를 시작합니다.'})


@app.route('/api/backtest/status')
def api_backtest_status():
    with bt_lock:
        return jsonify({
            'status': backtest_state['status'],
            'results': backtest_state['results'],
            'error_msg': backtest_state['error_msg'],
            'progress': backtest_state['progress'],
        })


@app.route('/api/backtest/csv')
def api_backtest_csv():
    """일자별 종목별 상세 데이터 CSV 다운로드"""
    import csv
    import io

    with bt_lock:
        engine = backtest_state.get('engine')
        results = backtest_state.get('results')

    if not engine or not results:
        return jsonify({'error': '백테스트 결과가 없습니다.'}), 404

    # 일자별 상세 데이터 생성
    daily_rows = engine.get_daily_detail()

    output = io.StringIO()
    # BOM for Excel 한글 호환
    output.write('\ufeff')

    writer = csv.writer(output)
    writer.writerow([
        '날짜', '종목코드', '종목명',
        '시가', '고가', '저가', '종가', '거래량',
        '매매구분', '매매수량', '체결가', '거래비용',
        '보유수량', '보유평가금액',
        '포트폴리오총자산', '포트폴리오현금',
    ])

    for row in daily_rows:
        writer.writerow([
            row['date'], row['ticker'], row['name'],
            row['open'], row['high'], row['low'], row['close'], row['volume'],
            row['action'], row['shares_traded'],
            row['exec_price'], row['trade_cost'],
            row['holding_shares'], row['holding_value'],
            row['portfolio_equity'], row['portfolio_cash'],
        ])

    # 매매 이력 시트 (별도 섹션)
    writer.writerow([])
    writer.writerow(['=== 매매 상세 이력 ==='])
    writer.writerow([
        '종목코드', '종목명',
        '매수일', '매수가', '매수수량',
        '매입금액', '평균단가', '총매입금액',
        '평가금액', '평가손익',
        '매도일', '매도가', '매도비용',
        '실현손익', '수익률(%)', '상태',
    ])
    for t in (results.get('trades') or []):
        writer.writerow([
            t['ticker'], t['name'],
            t['entry_date'], t['entry_price'], t['shares'],
            t['buy_amount'], t['avg_price'], t['total_buy_amount'],
            t['eval_amount'], t['eval_pnl'],
            t['exit_date'] or '', t['exit_price'] or '', t['exit_cost'],
            t['realized_pnl'] if t['realized_pnl'] is not None else '',
            t['return_pct'] if t['return_pct'] is not None else '',
            t['status'],
        ])

    csv_data = output.getvalue()
    output.close()

    # 파일명에 전략명과 날짜 포함
    config = results.get('config', {})
    strategy_name = config.get('strategy', 'backtest')
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'backtest_{strategy_name}_{timestamp}.csv'

    return Response(
        csv_data,
        mimetype='text/csv; charset=utf-8-sig',
        headers={'Content-Disposition': f'attachment; filename={filename}'}
    )


# ============================================================
# DuckDB 데이터 뷰어
# ============================================================
@app.route('/db')
def db_viewer():
    return render_template_string(DB_VIEWER_TEMPLATE)

@app.route('/api/db/tables')
def api_db_tables():
    """테이블 목록 및 DB 통계"""
    tables = stock_db.get_table_list()
    stats = stock_db.get_db_stats()
    return jsonify({'tables': tables, 'stats': stats})

@app.route('/api/db/schema/<table_name>')
def api_db_schema(table_name):
    """테이블 스키마 조회"""
    try:
        schema = stock_db.get_table_schema(table_name)
    except ValueError:
        return jsonify({'error': '존재하지 않는 테이블'}), 404
    return jsonify({'schema': schema})

@app.route('/api/db/query/<table_name>')
def api_db_query(table_name):
    """테이블 데이터 조회 (페이지네이션)"""
    try:
        page = int(request.args.get('page', 1))
        page_size = int(request.args.get('page_size', 50))
    except (TypeError, ValueError):
        return jsonify({'error': 'page와 page_size는 정수여야 합니다.'}), 400
    if page < 1 or not 1 <= page_size <= 500:
        return jsonify({'error': 'page는 1 이상, page_size는 1~500이어야 합니다.'}), 400

    order_by = request.args.get('order_by')
    order_dir = request.args.get('order_dir', 'DESC')
    filter_col = request.args.get('filter_col')
    filter_val = request.args.get('filter_val')

    try:
        result = stock_db.query_table(
            table_name, page, page_size,
            order_by, order_dir, filter_col, filter_val
        )
    except ValueError:
        return jsonify({'error': '존재하지 않는 테이블'}), 404
    return jsonify(result)

@app.route('/api/db/ticker-summary')
def api_db_ticker_summary():
    """종목별 데이터 요약"""
    return jsonify({'summary': stock_db.get_ticker_summary()})


# ============================================================
# HTML 템플릿 - 메인 스크리닝
# ============================================================
HTML_TEMPLATE = r'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>한국 증시 종합 스크리닝</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',sans-serif;background:#f0f2f5;color:#1a1a2e;line-height:1.6}
.wrap{max-width:1440px;margin:0 auto;padding:20px}
.hd{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:#fff;padding:30px 40px;border-radius:16px;margin-bottom:24px;box-shadow:0 4px 20px rgba(0,0,0,.15);display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:16px}
.hd-left h1{font-size:26px;margin-bottom:6px}.hd-left p{opacity:.8;font-size:13px}
.hd-right{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
.refresh-btn{
    padding:12px 28px;border:none;border-radius:10px;font-size:14px;font-weight:700;
    cursor:pointer;transition:all .3s;display:flex;align-items:center;gap:8px;
    background:linear-gradient(135deg,#22c55e,#16a34a);color:#fff;
    box-shadow:0 2px 12px rgba(34,197,94,.3);
}
.refresh-btn:hover{transform:translateY(-1px);box-shadow:0 4px 16px rgba(34,197,94,.4)}
.refresh-btn:active{transform:translateY(0)}
.refresh-btn:disabled{opacity:.6;cursor:not-allowed;transform:none}
.refresh-btn .spinner{display:none;width:16px;height:16px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin .8s linear infinite}
.refresh-btn.loading .spinner{display:inline-block}
.refresh-btn.loading .btn-icon{display:none}
@keyframes spin{to{transform:rotate(360deg)}}
.schedule-badge{background:rgba(255,255,255,.15);padding:6px 14px;border-radius:8px;font-size:12px;color:rgba(255,255,255,.9);display:flex;align-items:center;gap:6px}
.schedule-badge .dot{width:8px;height:8px;border-radius:50%;background:#22c55e;animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.update-info{font-size:12px;color:rgba(255,255,255,.7);text-align:right}
.sg{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px;margin-bottom:22px}
.sc{background:#fff;border-radius:12px;padding:18px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.06);transition:transform .2s}
.sc:hover{transform:translateY(-2px)}
.sc .n{font-size:30px;font-weight:700}.sc .l{font-size:12px;color:#666;margin-top:3px}
.sc.hl{border-left:4px solid #22c55e}
.s3 .n{color:#16a34a}.s2 .n{color:#d97706}.s1 .n{color:#6b7280}
.fb{background:#fff;border-radius:12px;padding:14px 20px;margin-bottom:18px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.fb label{font-weight:600;font-size:13px}
.fb button{padding:7px 14px;border:2px solid #e5e7eb;border-radius:8px;background:#fff;cursor:pointer;font-size:13px;transition:all .2s}
.fb button:hover{border-color:#302b63}.fb button.a{background:#302b63;color:#fff;border-color:#302b63}
.si{padding:7px 14px;border:2px solid #e5e7eb;border-radius:8px;font-size:13px;min-width:180px;outline:none}
.si:focus{border-color:#302b63}
.tn{display:flex;gap:3px;background:#fff;border-radius:12px 12px 0 0;padding:8px 8px 0;box-shadow:0 -2px 8px rgba(0,0,0,.04)}
.tb{padding:10px 22px;border:none;background:transparent;cursor:pointer;font-size:13px;font-weight:600;color:#666;border-radius:8px 8px 0 0;transition:all .2s}
.tb:hover{color:#302b63;background:#f8f9fa}.tb.a{color:#302b63;background:#f0f2f5;border-bottom:3px solid #302b63}
.tc{background:#fff;border-radius:0 0 12px 12px;overflow-x:auto;box-shadow:0 2px 8px rgba(0,0,0,.06);min-height:200px}
.tp{display:none}.tp.a{display:block}
table{width:100%;border-collapse:collapse;font-size:13px}
thead{background:#f8f9fa;position:sticky;top:0;z-index:10}
th{padding:12px 14px;text-align:left;font-weight:600;color:#374151;border-bottom:2px solid #e5e7eb;white-space:nowrap}
td{padding:10px 14px;border-bottom:1px solid #f3f4f6}
tr:hover{background:#f8fafc}.c{text-align:center}
.score-3{background:#f0fdf4}.score-3:hover{background:#dcfce7!important}
.score-2{background:#fffbeb}.score-2:hover{background:#fef3c7!important}
.score-1{background:#fff}
.badge{display:inline-block;padding:3px 10px;border-radius:20px;font-weight:700;font-size:12px}
.b3{background:#dcfce7;color:#16a34a}.b2{background:#fef3c7;color:#d97706}.b1{background:#f3f4f6;color:#6b7280}
.tag{display:inline-block;padding:2px 7px;border-radius:4px;font-size:10px;font-weight:600;margin:1px}
.tag.turn{background:#dbeafe;color:#2563eb}.tag.supply{background:#fce7f3;color:#db2777}.tag.nps{background:#d1fae5;color:#059669}
.sn{white-space:nowrap}.det{font-size:11px}
.d{display:inline-block;padding:2px 5px;margin:1px;border-radius:3px;font-size:10px;white-space:nowrap}
.d.turn{background:#eff6ff;color:#1d4ed8}.d.supply{background:#fff1f2;color:#be123c}.d.nps{background:#ecfdf5;color:#047857}
.st{font-size:12px}.st th{background:#f1f5f9;font-size:12px;padding:8px 10px}.st td{padding:7px 10px}
.ft{text-align:center;padding:20px;color:#9ca3af;font-size:11px}
.loading-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.3);z-index:1000;justify-content:center;align-items:center}
.loading-overlay.show{display:flex}
.loading-box{background:#fff;border-radius:16px;padding:40px;text-align:center;box-shadow:0 8px 40px rgba(0,0,0,.2)}
.loading-box .big-spinner{width:48px;height:48px;border:4px solid #e5e7eb;border-top-color:#302b63;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 16px}
.loading-box p{font-size:15px;font-weight:600;color:#374151}
.loading-box .sub{font-size:12px;color:#9ca3af;margin-top:4px}
.toast{position:fixed;top:20px;right:20px;padding:14px 24px;border-radius:10px;color:#fff;font-size:14px;font-weight:600;z-index:2000;transform:translateX(120%);transition:transform .4s ease;box-shadow:0 4px 16px rgba(0,0,0,.15)}
.toast.show{transform:translateX(0)}
.toast.success{background:#16a34a}.toast.error{background:#dc2626}.toast.info{background:#2563eb}
.empty-state{padding:60px 20px;text-align:center;color:#9ca3af}
.empty-state p{font-size:16px;margin-bottom:8px}
@media(max-width:768px){.wrap{padding:10px}.hd{padding:18px;flex-direction:column}.hd h1{font-size:18px}.sg{grid-template-columns:repeat(2,1fr);gap:8px}.fb{flex-direction:column}.si{min-width:100%}}
</style>
</head>
<body>
<div class="wrap">
    <div class="hd">
        <div class="hd-left">
            <h1>한국 증시 종합 스크리닝 시스템</h1>
            <p>턴어라운드(연간실적호전) + 외국인/기관 동반 순매수 전환 + 국민연금 신규/추가매수</p>
        </div>
        <div class="hd-right">
            <a href="/backtest" style="padding:10px 20px;border-radius:10px;font-size:13px;font-weight:700;background:rgba(255,255,255,.15);color:#fff;text-decoration:none;transition:all .3s">📊 백테스트</a>
            <a href="/db" style="padding:10px 20px;border-radius:10px;font-size:13px;font-weight:700;background:rgba(255,255,255,.15);color:#fff;text-decoration:none;transition:all .3s">💾 DB 뷰어</a>
            <div class="schedule-badge">
                <span class="dot"></span>
                매일 08:00 자동 갱신
            </div>
            <button class="refresh-btn" id="refreshBtn" onclick="doRefresh()">
                <span class="btn-icon">&#x21bb;</span>
                <span class="spinner"></span>
                재조회
            </button>
            <div class="update-info" id="updateInfo">로딩 중...</div>
        </div>
    </div>

    <div class="sg" id="statsGrid">
        <div class="sc s3 hl"><div class="n" id="stat3">-</div><div class="l">3점 (전체 해당)</div></div>
        <div class="sc s2"><div class="n" id="stat2">-</div><div class="l">2점 (2개 해당)</div></div>
        <div class="sc s1"><div class="n" id="stat1">-</div><div class="l">1점 (1개 해당)</div></div>
        <div class="sc"><div class="n" id="statTurn">-</div><div class="l">연간실적호전</div></div>
        <div class="sc"><div class="n" id="statSupply">-</div><div class="l">순매수전환</div></div>
        <div class="sc"><div class="n" id="statNps">-</div><div class="l">국민연금 신규/추가매수</div></div>
    </div>

    <div class="fb">
        <label>필터:</label>
        <button class="a" onclick="filt('all',this)">전체</button>
        <button onclick="filt(3,this)">3점</button>
        <button onclick="filt(2,this)">2점↑</button>
        <button onclick="filt(1,this)">1점↑</button>
        <input type="text" class="si" placeholder="종목명 검색..." oninput="srch(this.value)">
    </div>

    <div class="tn">
        <button class="tb a" onclick="showTab('m',this)">종합 결과</button>
        <button class="tb" id="tabTurn" onclick="showTab('t',this)">연간실적호전</button>
        <button class="tb" id="tabSupply" onclick="showTab('s',this)">순매수전환</button>
        <button class="tb" id="tabNps" onclick="showTab('n',this)">국민연금 매수</button>
    </div>

    <div class="tc">
        <div id="m" class="tp a">
            <table><thead><tr>
                <th style="width:45px" class="c">No.</th>
                <th style="width:130px">종목명</th>
                <th style="width:70px" class="c">점수</th>
                <th style="width:180px">해당 항목</th>
                <th>상세 정보</th>
            </tr></thead>
            <tbody id="mainBody"></tbody></table>
        </div>
        <div id="t" class="tp">
            <h3 style="padding:14px 14px 0;color:#2563eb">연간실적호전 종목 (단위: 억원, 배)</h3>
            <table class="st"><thead id="turnHead"></thead><tbody id="turnBody"></tbody></table>
        </div>
        <div id="s" class="tp">
            <h3 style="padding:14px 14px 0;color:#db2777">외국인/기관 동반 순매수 전환 종목</h3>
            <table class="st"><thead id="supplyHead"></thead><tbody id="supplyBody"></tbody></table>
        </div>
        <div id="n" class="tp">
            <h3 style="padding:14px 14px 0;color:#059669">국민연금 신규/추가매수 신호</h3>
            <p style="padding:6px 14px 0;color:#6b7280;font-size:12px">FnGuide 공개 주요주주 범위의 국민연금 신규·추가매수 신호는 매수일부터 3개월 동안만 1점으로 반영됩니다.</p>
            <table class="st"><thead id="npsHead"></thead><tbody id="npsBody"></tbody></table>
        </div>
    </div>

    <div class="ft">데이터 출처: FnGuide (comp.fnguide.com) | 투자 참고용이며, 투자의 최종 책임은 투자자 본인에게 있습니다.</div>
</div>

<div class="loading-overlay" id="loadingOverlay">
    <div class="loading-box">
        <div class="big-spinner"></div>
        <p>데이터 갱신 중...</p>
        <div class="sub">FnGuide에서 최신 데이터를 수집하고 있습니다</div>
    </div>
</div>

<div class="toast" id="toast"></div>

<script>
let pollTimer = null;

// 페이지 로드 시 데이터 가져오기
window.addEventListener('DOMContentLoaded', () => { fetchStatus(); });

function showToast(msg, type='info') {
    const t = document.getElementById('toast');
    t.textContent = msg;
    t.className = 'toast ' + type + ' show';
    setTimeout(() => t.classList.remove('show'), 3000);
}

function doRefresh() {
    const btn = document.getElementById('refreshBtn');
    btn.classList.add('loading');
    btn.disabled = true;
    document.getElementById('loadingOverlay').classList.add('show');

    fetch('/api/refresh', { method: 'POST' })
        .then(r => r.json())
        .then(d => {
            if (d.status === 'already_loading') {
                showToast('이미 갱신 중입니다.', 'info');
            } else {
                showToast('데이터 갱신을 시작합니다...', 'info');
            }
            // 폴링 시작
            if (pollTimer) clearInterval(pollTimer);
            pollTimer = setInterval(pollStatus, 2000);
        })
        .catch(e => {
            showToast('갱신 요청 실패: ' + e.message, 'error');
            btn.classList.remove('loading');
            btn.disabled = false;
            document.getElementById('loadingOverlay').classList.remove('show');
        });
}

function pollStatus() {
    fetch('/api/status')
        .then(r => r.json())
        .then(d => {
            if (d.status === 'done') {
                clearInterval(pollTimer);
                pollTimer = null;
                document.getElementById('refreshBtn').classList.remove('loading');
                document.getElementById('refreshBtn').disabled = false;
                document.getElementById('loadingOverlay').classList.remove('show');
                renderData(d);
                if (d.error_msg) {
                    showToast(d.error_msg, 'error');
                } else {
                    showToast('데이터 갱신 완료!', 'success');
                }
            } else if (d.status === 'error') {
                clearInterval(pollTimer);
                pollTimer = null;
                document.getElementById('refreshBtn').classList.remove('loading');
                document.getElementById('refreshBtn').disabled = false;
                document.getElementById('loadingOverlay').classList.remove('show');
                showToast('갱신 실패: ' + d.error_msg, 'error');
            }
        });
}

function fetchStatus() {
    fetch('/api/status')
        .then(r => r.json())
        .then(d => {
            if (d.status === 'done' && d.result && d.result.length > 0) {
                renderData(d);
            } else if (d.status === 'loading') {
                document.getElementById('refreshBtn').classList.add('loading');
                document.getElementById('refreshBtn').disabled = true;
                document.getElementById('loadingOverlay').classList.add('show');
                pollTimer = setInterval(pollStatus, 2000);
            } else {
                document.getElementById('mainBody').innerHTML =
                    '<tr><td colspan="5" class="empty-state"><p>데이터가 없습니다</p><p style="font-size:13px">재조회 버튼을 눌러 데이터를 수집하세요</p></td></tr>';
            }
        });
}

function escapeHtml(value) {
    const element = document.createElement('div');
    element.textContent = value == null ? '' : String(value);
    return element.innerHTML;
}

function renderData(d) {
    const stats = d.stats || {};
    document.getElementById('stat3').textContent = stats.score_3 || 0;
    document.getElementById('stat2').textContent = stats.score_2 || 0;
    document.getElementById('stat1').textContent = stats.score_1 || 0;
    document.getElementById('statTurn').textContent = stats.turn_count || 0;
    document.getElementById('statSupply').textContent = stats.supply_count || 0;
    document.getElementById('statNps').textContent = stats.nps_count || 0;
    document.getElementById('updateInfo').textContent = '마지막 갱신: ' + (d.last_updated || '-');
    document.getElementById('tabTurn').textContent = '연간실적호전 (' + (stats.turn_count||0) + ')';
    document.getElementById('tabSupply').textContent = '순매수전환 (' + (stats.supply_count||0) + ')';
    document.getElementById('tabNps').textContent = '국민연금 매수 (' + (stats.nps_count||0) + ')';

    // 메인 테이블
    const body = document.getElementById('mainBody');
    body.innerHTML = '';
    (d.result || []).forEach((r, i) => {
        const s = r['종합점수'];
        let tags = '';
        (r['출처'] || '').split(', ').forEach(src => {
            const cls = src.includes('실적') ? 'turn' : (src.includes('순매수') ? 'supply' : 'nps');
            tags += `<span class="tag ${cls}">${escapeHtml(src)}</span> `;
        });
        let details = '';
        Object.keys(r).forEach(k => {
            const v = r[k];
            if (!v || v === '') return;
            if (k.startsWith('[턴]')) details += `<span class="d turn">${escapeHtml(k.slice(3))}: ${escapeHtml(v)}</span> `;
            else if (k.startsWith('[수급]')) details += `<span class="d supply">${escapeHtml(k.slice(4))}: ${escapeHtml(v)}</span> `;
            else if (k.startsWith('[연금]')) details += `<span class="d nps">${escapeHtml(k.slice(4))}: ${escapeHtml(v)}</span> `;
        });
        body.innerHTML += `<tr class="score-${s}" data-score="${s}">
            <td class="c">${i+1}</td>
            <td class="sn"><b>${escapeHtml(r['종목명'])}</b></td>
            <td class="c"><span class="badge b${s}">${s}점</span></td>
            <td>${tags}</td>
            <td class="det">${details}</td>
        </tr>`;
    });

    // 서브 테이블들
    renderSubTable(d.turn || [], 'turnHead', 'turnBody');
    renderSubTable(d.supply || [], 'supplyHead', 'supplyBody');
    renderSubTable(d.nps || [], 'npsHead', 'npsBody');
}

function renderSubTable(data, headId, bodyId) {
    if (!data.length) return;
    const cols = Object.keys(data[0]).filter(c => c !== 'No.');
    document.getElementById(headId).innerHTML = '<tr>' + cols.map(c => `<th>${escapeHtml(c)}</th>`).join('') + '</tr>';
    document.getElementById(bodyId).innerHTML = data.map(r =>
        '<tr>' + cols.map(c => `<td>${escapeHtml(r[c] || '')}</td>`).join('') + '</tr>'
    ).join('');
}

function filt(v, btn) {
    document.querySelectorAll('.fb button').forEach(b => b.classList.remove('a'));
    if (btn) btn.classList.add('a');
    document.querySelectorAll('#mainBody tr').forEach(r => {
        const s = +r.dataset.score;
        r.style.display = v === 'all' || s >= v ? '' : 'none';
    });
}

function srch(q) {
    q = q.trim().toLowerCase();
    document.querySelectorAll('#mainBody tr').forEach(r => {
        r.style.display = r.querySelector('.sn').textContent.toLowerCase().includes(q) ? '' : 'none';
    });
}

function showTab(id, btn) {
    document.querySelectorAll('.tp').forEach(t => t.classList.remove('a'));
    document.querySelectorAll('.tb').forEach(b => b.classList.remove('a'));
    document.getElementById(id).classList.add('a');
    if (btn) btn.classList.add('a');
}
</script>
</body>
</html>'''


# ============================================================
# HTML 템플릿 - 백테스트
# ============================================================
BACKTEST_TEMPLATE = r'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>백테스트 - 한국 증시 스크리닝</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',sans-serif;background:#f0f2f5;color:#1a1a2e;line-height:1.6}
.wrap{max-width:1440px;margin:0 auto;padding:20px}
.hd{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:#fff;padding:30px 40px;border-radius:16px;margin-bottom:24px;box-shadow:0 4px 20px rgba(0,0,0,.15)}
.hd-top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:16px}
.hd h1{font-size:24px}.hd p{opacity:.8;font-size:13px;margin-top:4px}
.hd-nav{display:flex;gap:10px;align-items:center}
.hd-nav a{padding:10px 20px;border-radius:10px;font-size:13px;font-weight:700;text-decoration:none;transition:all .3s}
.nav-back{background:rgba(255,255,255,.15);color:#fff}
.nav-back:hover{background:rgba(255,255,255,.25)}
.config{background:#fff;border-radius:12px;padding:20px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,.06);display:flex;gap:16px;align-items:flex-end;flex-wrap:wrap}
.cfg-group{display:flex;flex-direction:column;gap:4px}
.cfg-group label{font-size:12px;font-weight:600;color:#666}
.cfg-group select,.cfg-group input{padding:8px 14px;border:2px solid #e5e7eb;border-radius:8px;font-size:13px;outline:none}
.cfg-group select:focus,.cfg-group input:focus{border-color:#302b63}
.run-btn{padding:10px 28px;border:none;border-radius:10px;font-size:14px;font-weight:700;cursor:pointer;background:linear-gradient(135deg,#6366f1,#4f46e5);color:#fff;transition:all .3s;display:flex;align-items:center;gap:8px}
.run-btn:hover{transform:translateY(-1px);box-shadow:0 4px 16px rgba(99,102,241,.4)}
.run-btn:disabled{opacity:.6;cursor:not-allowed;transform:none}
.run-btn .spinner{display:none;width:16px;height:16px;border:2px solid rgba(255,255,255,.3);border-top-color:#fff;border-radius:50%;animation:spin .8s linear infinite}
.run-btn.loading .spinner{display:inline-block}
.run-btn.loading .btn-text{display:none}
@keyframes spin{to{transform:rotate(360deg)}}
.progress-bar{display:none;background:#fff;border-radius:12px;padding:16px 20px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.progress-bar.show{display:block}
.progress-bar .ptext{font-size:13px;color:#374151;font-weight:500}
.sg{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:22px}
.sc{background:#fff;border-radius:12px;padding:18px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.06);transition:transform .2s}
.sc:hover{transform:translateY(-2px)}
.sc .n{font-size:28px;font-weight:700}.sc .l{font-size:11px;color:#666;margin-top:3px}
.pos .n{color:#16a34a}.neg .n{color:#dc2626}.neu .n{color:#374151}
.chart-row{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:22px}
.chart-box{background:#fff;border-radius:12px;padding:20px;box-shadow:0 2px 8px rgba(0,0,0,.06)}
.chart-box h3{font-size:14px;font-weight:600;color:#374151;margin-bottom:12px}
.chart-box canvas{width:100%!important;height:300px!important}
.tbl-box{background:#fff;border-radius:12px;overflow-x:auto;box-shadow:0 2px 8px rgba(0,0,0,.06);margin-bottom:20px}
.tbl-box h3{padding:16px 20px 8px;font-size:14px;font-weight:600;color:#374151}
table{width:100%;border-collapse:collapse;font-size:13px}
thead{background:#f8f9fa;position:sticky;top:0}
th{padding:10px 14px;text-align:left;font-weight:600;color:#374151;border-bottom:2px solid #e5e7eb;white-space:nowrap}
td{padding:9px 14px;border-bottom:1px solid #f3f4f6}
tr:hover{background:#f8fafc}
.c{text-align:center}.r{text-align:right}
.pos-text{color:#16a34a;font-weight:600}.neg-text{color:#dc2626;font-weight:600}
.ft{text-align:center;padding:20px;color:#9ca3af;font-size:11px}
.disclaimer{background:#fffbeb;border:1px solid #fde68a;border-radius:10px;padding:12px 16px;margin-bottom:20px;font-size:12px;color:#92400e}
.empty{padding:80px 20px;text-align:center;color:#9ca3af}
.empty p{font-size:15px;margin-bottom:6px}
.toast{position:fixed;top:20px;right:20px;padding:14px 24px;border-radius:10px;color:#fff;font-size:14px;font-weight:600;z-index:2000;transform:translateX(120%);transition:transform .4s;box-shadow:0 4px 16px rgba(0,0,0,.15)}
.toast.show{transform:translateX(0)}.toast.success{background:#16a34a}.toast.error{background:#dc2626}.toast.info{background:#2563eb}
@media(max-width:900px){.chart-row{grid-template-columns:1fr}.sg{grid-template-columns:repeat(2,1fr)}.config{flex-direction:column;align-items:stretch}}
</style>
</head>
<body>
<div class="wrap">
    <div class="hd">
        <div class="hd-top">
            <div>
                <h1>백테스트</h1>
                <p>스크리닝 2점 이상 종목의 과거 성과를 시뮬레이션합니다</p>
            </div>
            <div class="hd-nav">
                <a href="/" class="nav-back">← 스크리닝 대시보드</a>
            </div>
        </div>
    </div>

    <div class="disclaimer">
        ⚠️ <b>참고:</b> 본 백테스트는 현재 스크리닝 결과 기준으로 과거 데이터를 시뮬레이션한 것입니다.
        실제 과거 시점의 스크리닝 결과와 다를 수 있으며 (Look-ahead bias), 투자 성과를 보장하지 않습니다.
    </div>

    <div class="config">
        <div class="cfg-group">
            <label>백테스트 기간</label>
            <select id="cfgPeriod">
                <option value="3">3개월</option>
                <option value="6" selected>6개월</option>
                <option value="12">1년</option>
                <option value="24">2년</option>
            </select>
        </div>
        <div class="cfg-group">
            <label>초기 투자금액 (원)</label>
            <input type="number" id="cfgCapital" value="100000000" step="10000000" min="10000000">
        </div>
        <div class="cfg-group">
            <label>전략</label>
            <select id="cfgStrategy">
                <option value="equal_weight">동일 비중 Buy & Hold</option>
                <option value="rebalance">월간 리밸런싱 (20일)</option>
                <option value="vol_trailing_stop">🛡️ 변동성 가중 + 트레일링 스탑</option>
                <option value="ma_filter">📊 이동평균 필터 (MA20)</option>
                <option value="composite">🔒 복합 전략 (MA + 변동성 + 스탑)</option>
            </select>
        </div>
        <div class="cfg-group">
            <label>슬리피지 (%)</label>
            <input type="number" id="cfgSlippage" value="0.3" step="0.05" min="0" max="5" style="width:90px">
        </div>
        <div class="cfg-group">
            <label>거래 수수료 (%)</label>
            <input type="number" id="cfgCommission" value="0.015" step="0.001" min="0" max="1" style="width:90px">
        </div>
        <div class="cfg-group">
            <label>증권거래세 (%)</label>
            <input type="number" id="cfgTax" value="0.20" step="0.01" min="0" max="1" style="width:90px">
        </div>
        <button class="run-btn" id="runBtn" onclick="runBacktest()">
            <span class="btn-text">백테스트 실행</span>
            <span class="spinner"></span>
        </button>
    </div>

    <div class="progress-bar" id="progressBar">
        <div class="ptext" id="progressText">준비 중...</div>
    </div>

    <div id="resultsArea" style="display:none">
        <div class="sg" id="metricsGrid"></div>
        <div id="costBox" style="background:#fff;border-radius:12px;padding:16px 20px;margin-bottom:20px;box-shadow:0 2px 8px rgba(0,0,0,.06)">
            <h3 style="font-size:14px;font-weight:600;color:#374151;margin-bottom:10px">거래 비용 내역</h3>
            <div id="costDetail" style="display:flex;gap:24px;flex-wrap:wrap;font-size:13px"></div>
        </div>
        <div class="chart-row">
            <div class="chart-box">
                <h3>수익률 곡선 (Equity Curve)</h3>
                <canvas id="equityChart"></canvas>
            </div>
            <div class="chart-box">
                <h3>낙폭 (Drawdown)</h3>
                <canvas id="ddChart"></canvas>
            </div>
        </div>
        <div class="tbl-box">
            <h3>종목별 성과</h3>
            <table>
                <thead><tr>
                    <th>종목명</th><th>종목코드</th>
                    <th class="r">시작가</th><th class="r">종료가</th>
                    <th class="r">수익률</th><th class="r">MDD</th>
                </tr></thead>
                <tbody id="stockBody"></tbody>
            </table>
        </div>

        <div class="tbl-box" id="tradeHistoryBox">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px">
                <h3 style="margin:0">종목별 매수/매도 상세 이력</h3>
                <button onclick="downloadCSV()" style="background:#16a34a;color:#fff;border:none;padding:8px 16px;border-radius:8px;font-size:13px;cursor:pointer;font-weight:600">CSV 다운로드</button>
            </div>
            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-bottom:12px;padding:10px 14px;background:#f8f9fa;border-radius:8px">
                <select id="tradeStockFilter" onchange="filterTrades()" style="padding:5px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px">
                    <option value="all">전체 종목</option>
                </select>
                <select id="tradeStatusFilter" onchange="filterTrades()" style="padding:5px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px">
                    <option value="all">전체 상태</option>
                    <option value="closed">청산</option>
                    <option value="open">보유중</option>
                </select>
                <select id="tradePnlFilter" onchange="filterTrades()" style="padding:5px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px">
                    <option value="all">손익 전체</option>
                    <option value="profit">수익 (실현손익 +)</option>
                    <option value="loss">손실 (실현손익 -)</option>
                </select>
                <input type="date" id="tradeDateFrom" onchange="filterTrades()" placeholder="시작일" style="padding:5px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px" title="매수일 시작">
                <span style="font-size:12px;color:#999">~</span>
                <input type="date" id="tradeDateTo" onchange="filterTrades()" placeholder="종료일" style="padding:5px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px" title="매수일 종료">
                <button onclick="resetTradeFilters()" style="padding:5px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;background:#fff;cursor:pointer">초기화</button>
                <span id="tradeFilterCount" style="font-size:11px;color:#6b7280;margin-left:auto"></span>
            </div>
            <div style="overflow-x:auto">
            <table id="tradeTable" style="font-size:12px">
                <thead><tr>
                    <th>종목코드</th><th>종목명</th>
                    <th class="c">매수일</th><th class="r">매수가</th><th class="r">매수수량</th>
                    <th class="r">매입금액</th><th class="r">평균단가</th><th class="r">총매입금액</th>
                    <th class="r">평가금액</th><th class="r">평가손익</th>
                    <th class="c">매도일</th><th class="r">매도가</th><th class="r">매도비용</th>
                    <th class="r">실현손익</th><th class="r">수익률(%)</th><th class="c">상태</th>
                </tr></thead>
                <tbody id="tradeBody"></tbody>
            </table>
            </div>
        </div>
    </div>

    <div id="emptyState" class="empty">
        <p>백테스트 결과가 없습니다</p>
        <p style="font-size:13px;color:#bbb">위 설정을 확인한 후 '백테스트 실행' 버튼을 클릭하세요</p>
    </div>

    <div class="ft">데이터 출처: KRX (pykrx) | 투자 참고용이며, 투자의 최종 책임은 투자자 본인에게 있습니다.</div>
</div>
<div class="toast" id="toast"></div>

<script>
let pollTimer = null;
let equityChartObj = null;
let ddChartObj = null;

// 페이지 로드 시 기존 결과 확인
window.addEventListener('DOMContentLoaded', () => {
    fetch('/api/backtest/status').then(r=>r.json()).then(d => {
        if (d.status === 'done' && d.results) renderResults(d.results);
        else if (d.status === 'loading') startPolling();
    });
});

function showToast(msg, type) {
    const t = document.getElementById('toast');
    t.textContent = msg; t.className = 'toast ' + type + ' show';
    setTimeout(() => t.classList.remove('show'), 3500);
}

function fmt(n) { return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ','); }

function runBacktest() {
    const btn = document.getElementById('runBtn');
    btn.classList.add('loading'); btn.disabled = true;
    document.getElementById('progressBar').classList.add('show');
    document.getElementById('emptyState').style.display = 'none';

    const body = JSON.stringify({
        period: +document.getElementById('cfgPeriod').value,
        capital: +document.getElementById('cfgCapital').value,
        strategy: document.getElementById('cfgStrategy').value,
        slippage: +document.getElementById('cfgSlippage').value,
        commission: +document.getElementById('cfgCommission').value,
        tax: +document.getElementById('cfgTax').value,
    });

    fetch('/api/backtest/run', {method:'POST', headers:{'Content-Type':'application/json'}, body})
        .then(r => r.json())
        .then(d => {
            if (d.status === 'already_loading') showToast('이미 실행 중입니다', 'info');
            else showToast('백테스트를 시작합니다...', 'info');
            startPolling();
        })
        .catch(e => {
            showToast('요청 실패: ' + e.message, 'error');
            resetBtn();
        });
}

function startPolling() {
    const btn = document.getElementById('runBtn');
    btn.classList.add('loading'); btn.disabled = true;
    document.getElementById('progressBar').classList.add('show');
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(pollStatus, 1500);
}

function pollStatus() {
    fetch('/api/backtest/status').then(r=>r.json()).then(d => {
        if (d.progress) document.getElementById('progressText').textContent = d.progress;
        if (d.status === 'done') {
            clearInterval(pollTimer); pollTimer = null;
            resetBtn();
            document.getElementById('progressBar').classList.remove('show');
            if (d.results) { renderResults(d.results); showToast('백테스트 완료!', 'success'); }
        } else if (d.status === 'error') {
            clearInterval(pollTimer); pollTimer = null;
            resetBtn();
            document.getElementById('progressBar').classList.remove('show');
            showToast('실패: ' + d.error_msg, 'error');
            document.getElementById('emptyState').style.display = '';
        }
    });
}

function resetBtn() {
    const btn = document.getElementById('runBtn');
    btn.classList.remove('loading'); btn.disabled = false;
}

function renderResults(r) {
    document.getElementById('resultsArea').style.display = '';
    document.getElementById('emptyState').style.display = 'none';

    const m = r.metrics;
    const posNeg = v => v >= 0 ? 'pos' : 'neg';

    const pl = m.profit_loss || (m.final_equity - m.initial_capital);
    const plSign = pl >= 0 ? '+' : '';

    // 메트릭 카드
    const grid = document.getElementById('metricsGrid');
    grid.innerHTML = `
        <div class="sc neu" style="border-left:4px solid #302b63"><div class="n">${fmt(m.initial_capital)}</div><div class="l">초기 투자금액</div></div>
        <div class="sc ${posNeg(pl)}" style="border-left:4px solid ${pl >= 0 ? '#16a34a' : '#dc2626'}"><div class="n">${fmt(m.current_value || m.final_equity)}</div><div class="l">현재가치</div></div>
        <div class="sc ${posNeg(pl)}"><div class="n">${plSign}${fmt(pl)}</div><div class="l">손익 (원)</div></div>
        <div class="sc ${posNeg(m.total_return)}"><div class="n">${m.total_return}%</div><div class="l">총 수익률</div></div>
        <div class="sc ${posNeg(m.annual_return)}"><div class="n">${m.annual_return}%</div><div class="l">연환산 수익률</div></div>
        <div class="sc neg"><div class="n">${m.mdd}%</div><div class="l">MDD</div></div>
        <div class="sc neu"><div class="n">${m.sharpe}</div><div class="l">Sharpe Ratio</div></div>
        <div class="sc neu"><div class="n">${m.volatility}%</div><div class="l">변동성 (연)</div></div>
        <div class="sc neu"><div class="n">${m.trading_days}일</div><div class="l">거래일수</div></div>
    `;
    if (r.benchmark) {
        grid.innerHTML += `<div class="sc ${posNeg(r.benchmark.return_pct)}"><div class="n">${r.benchmark.return_pct}%</div><div class="l">KOSPI 수익률</div></div>`;
    }

    // 거래 비용 내역
    const cc = r.cost_config || {};
    const cs = r.cost_summary || {};
    const costEl = document.getElementById('costDetail');
    costEl.innerHTML = `
        <div><b>슬리피지</b> (${cc.slippage_pct || 0}%): <span style="color:#dc2626">${fmt(cs.slippage || 0)}원</span></div>
        <div><b>거래 수수료</b> (${cc.commission_pct || 0}%): <span style="color:#dc2626">${fmt(cs.commission || 0)}원</span></div>
        <div><b>증권거래세</b> (${cc.tax_pct || 0}%): <span style="color:#dc2626">${fmt(cs.tax || 0)}원</span></div>
        <div style="font-weight:700"><b>총 거래비용</b>: <span style="color:#dc2626">${fmt(cs.total || 0)}원</span></div>
    `;

    renderEquityChart(r);
    renderDDChart(r);
    renderStockTable(r);
    renderTradeHistory(r);
}

function renderEquityChart(r) {
    const ctx = document.getElementById('equityChart').getContext('2d');
    if (equityChartObj) equityChartObj.destroy();

    const labels = r.equity_curve.map(d => d.date);
    const datasets = [{
        label: '포트폴리오',
        data: r.equity_curve.map(d => d.equity),
        borderColor: '#4f46e5', backgroundColor: 'rgba(79,70,229,.08)',
        fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2,
    }];

    if (r.benchmark && r.benchmark.curve) {
        // 벤치마크 날짜를 포트폴리오 날짜에 맞춰 보간
        const bMap = {}; r.benchmark.curve.forEach(b => bMap[b.date] = b.equity);
        datasets.push({
            label: 'KOSPI',
            data: labels.map(d => bMap[d] || null),
            borderColor: '#9ca3af', borderDash: [5,3],
            fill: false, tension: 0.3, pointRadius: 0, borderWidth: 1.5,
        });
    }

    equityChartObj = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets },
        options: {
            responsive: true, maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                tooltip: {
                    callbacks: {
                        label: ctx => ctx.dataset.label + ': ' + fmt(Math.round(ctx.parsed.y)) + '원'
                    }
                }
            },
            scales: {
                x: { display: true, ticks: { maxTicksLimit: 8, font: { size: 10 } } },
                y: {
                    display: true,
                    ticks: {
                        callback: v => (v / 100000000).toFixed(1) + '억',
                        font: { size: 10 }
                    }
                }
            }
        }
    });
}

function renderDDChart(r) {
    const ctx = document.getElementById('ddChart').getContext('2d');
    if (ddChartObj) ddChartObj.destroy();

    ddChartObj = new Chart(ctx, {
        type: 'line',
        data: {
            labels: r.drawdown_curve.map(d => d.date),
            datasets: [{
                label: 'Drawdown',
                data: r.drawdown_curve.map(d => d.dd),
                borderColor: '#dc2626', backgroundColor: 'rgba(220,38,38,.1)',
                fill: true, tension: 0.3, pointRadius: 0, borderWidth: 1.5,
            }]
        },
        options: {
            responsive: true, maintainAspectRatio: false,
            plugins: {
                tooltip: {
                    callbacks: {
                        label: ctx => 'DD: ' + ctx.parsed.y.toFixed(2) + '%'
                    }
                }
            },
            scales: {
                x: { display: true, ticks: { maxTicksLimit: 8, font: { size: 10 } } },
                y: { display: true, ticks: { callback: v => v.toFixed(0) + '%', font: { size: 10 } } }
            }
        }
    });
}

function renderStockTable(r) {
    const body = document.getElementById('stockBody');
    body.innerHTML = '';
    (r.stock_performance || []).forEach(s => {
        const retCls = s.return_pct >= 0 ? 'pos-text' : 'neg-text';
        body.innerHTML += `<tr>
            <td><b>${s.name}</b></td>
            <td class="c">${s.ticker}</td>
            <td class="r">${fmt(s.start_price)}</td>
            <td class="r">${fmt(s.end_price)}</td>
            <td class="r ${retCls}">${s.return_pct > 0 ? '+' : ''}${s.return_pct}%</td>
            <td class="r neg-text">${s.mdd}%</td>
        </tr>`;
    });
}

// 전역 변수로 trades 보관
let _allTrades = [];

function renderTradeHistory(r) {
    const trades = r.trades || [];
    _allTrades = trades;

    // 종목 필터 드롭다운 채우기
    const filter = document.getElementById('tradeStockFilter');
    const stockNames = new Map();
    trades.forEach(t => { if (!stockNames.has(t.ticker)) stockNames.set(t.ticker, t.name); });
    filter.innerHTML = '<option value="all">전체 종목</option>';
    stockNames.forEach((name, ticker) => {
        filter.innerHTML += `<option value="${ticker}">${name} (${ticker})</option>`;
    });

    renderTradeRows(trades);
}

function filterTrades() {
    const stock = document.getElementById('tradeStockFilter').value;
    const status = document.getElementById('tradeStatusFilter').value;
    const pnl = document.getElementById('tradePnlFilter').value;
    const dateFrom = document.getElementById('tradeDateFrom').value;
    const dateTo = document.getElementById('tradeDateTo').value;

    let filtered = _allTrades;
    if (stock !== 'all') filtered = filtered.filter(t => t.ticker === stock);
    if (status !== 'all') filtered = filtered.filter(t => t.status === status);
    if (pnl === 'profit') filtered = filtered.filter(t => t.realized_pnl != null && t.realized_pnl > 0);
    if (pnl === 'loss') filtered = filtered.filter(t => t.realized_pnl != null && t.realized_pnl < 0);
    if (dateFrom) filtered = filtered.filter(t => t.entry_date >= dateFrom);
    if (dateTo) filtered = filtered.filter(t => t.entry_date <= dateTo);

    document.getElementById('tradeFilterCount').textContent =
        filtered.length === _allTrades.length ? '' : filtered.length + '/' + _allTrades.length + '건';
    renderTradeRows(filtered);
}

function resetTradeFilters() {
    document.getElementById('tradeStockFilter').value = 'all';
    document.getElementById('tradeStatusFilter').value = 'all';
    document.getElementById('tradePnlFilter').value = 'all';
    document.getElementById('tradeDateFrom').value = '';
    document.getElementById('tradeDateTo').value = '';
    document.getElementById('tradeFilterCount').textContent = '';
    renderTradeRows(_allTrades);
}

function renderTradeRows(trades) {
    const body = document.getElementById('tradeBody');
    body.innerHTML = '';

    if (!trades.length) {
        body.innerHTML = '<tr><td colspan="16" class="c" style="color:#999;padding:20px">매매 이력이 없습니다</td></tr>';
        return;
    }

    trades.forEach(t => {
        const evalCls = (t.eval_pnl || 0) >= 0 ? 'pos-text' : 'neg-text';
        const realCls = (t.realized_pnl || 0) >= 0 ? 'pos-text' : 'neg-text';
        const retCls = (t.return_pct || 0) >= 0 ? 'pos-text' : 'neg-text';
        const statusBadge = t.status === 'closed'
            ? '<span style="background:#e0e7ff;color:#4338ca;padding:2px 8px;border-radius:10px;font-size:11px">청산</span>'
            : '<span style="background:#fef3c7;color:#d97706;padding:2px 8px;border-radius:10px;font-size:11px">보유중</span>';

        const fmtPnl = (v) => v != null ? ((v >= 0 ? '+' : '') + fmt(v)) : '-';
        const fmtPct = (v) => v != null ? ((v >= 0 ? '+' : '') + v + '%') : '-';

        body.innerHTML += `<tr>
            <td class="c" style="font-size:11px;color:#6b7280">${t.ticker}</td>
            <td><b>${t.name}</b></td>
            <td class="c">${t.entry_date}</td>
            <td class="r">${fmt(t.entry_price)}</td>
            <td class="r">${fmt(t.shares)}</td>
            <td class="r">${fmt(t.buy_amount)}</td>
            <td class="r">${fmt(t.avg_price)}</td>
            <td class="r">${fmt(t.total_buy_amount)}</td>
            <td class="r">${fmt(t.eval_amount)}</td>
            <td class="r ${evalCls}"><b>${fmtPnl(t.eval_pnl)}</b></td>
            <td class="c">${t.exit_date || '-'}</td>
            <td class="r">${t.exit_price ? fmt(t.exit_price) : '-'}</td>
            <td class="r" style="color:#dc2626">${t.exit_cost ? fmt(t.exit_cost) : '-'}</td>
            <td class="r ${realCls}"><b>${fmtPnl(t.realized_pnl)}</b></td>
            <td class="r ${retCls}">${fmtPct(t.return_pct)}</td>
            <td class="c">${statusBadge}</td>
        </tr>`;
    });
}

function downloadCSV() {
    window.location.href = '/api/backtest/csv';
}
</script>
</body>
</html>'''


# ============================================================
# HTML 템플릿 - DuckDB 뷰어
# ============================================================
DB_VIEWER_TEMPLATE = r'''<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DB 뷰어 - 한국 증시 스크리닝</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans KR',sans-serif;background:#f0f2f5;color:#1a1a2e;line-height:1.6}
.wrap{max-width:1440px;margin:0 auto;padding:20px}
.hd{background:linear-gradient(135deg,#0f0c29,#302b63,#24243e);color:#fff;padding:30px 40px;border-radius:16px;margin-bottom:24px;box-shadow:0 4px 20px rgba(0,0,0,.15)}
.hd-top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:16px}
.hd h1{font-size:24px}.hd p{opacity:.8;font-size:13px;margin-top:4px}
.hd-nav{display:flex;gap:10px;align-items:center}
.hd-nav a{padding:10px 20px;border-radius:10px;font-size:13px;font-weight:700;text-decoration:none;transition:all .3s;background:rgba(255,255,255,.15);color:#fff}
.hd-nav a:hover{background:rgba(255,255,255,.25)}
.sg{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:22px}
.sc{background:#fff;border-radius:12px;padding:18px;text-align:center;box-shadow:0 2px 8px rgba(0,0,0,.06);transition:transform .2s}
.sc:hover{transform:translateY(-2px)}
.sc .n{font-size:26px;font-weight:700;color:#302b63}.sc .l{font-size:12px;color:#666;margin-top:3px}
.main-grid{display:grid;grid-template-columns:260px 1fr;gap:20px;align-items:start}
.sidebar{background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.06);overflow:hidden}
.sidebar h3{padding:14px 16px;font-size:13px;font-weight:700;color:#374151;background:#f8f9fa;border-bottom:1px solid #e5e7eb}
.tbl-item{padding:10px 16px;font-size:13px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #f3f4f6;transition:background .15s}
.tbl-item:hover{background:#f0f2f5}
.tbl-item.active{background:#ede9fe;color:#4c1d95;font-weight:600}
.tbl-item .tbl-name{font-weight:500}
.tbl-item .row-cnt{font-size:11px;color:#9ca3af;background:#f3f4f6;padding:2px 7px;border-radius:10px}
.tbl-item.active .row-cnt{background:#ddd6fe;color:#5b21b6}
.content-panel{background:#fff;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.06);overflow:hidden}
.panel-header{padding:14px 20px;border-bottom:1px solid #e5e7eb;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;background:#f8f9fa}
.panel-header h3{font-size:14px;font-weight:700;color:#374151}
.tab-bar{display:flex;gap:4px;background:#f3f4f6;border-radius:8px;padding:3px}
.tab-btn{padding:6px 16px;border:none;background:transparent;cursor:pointer;font-size:13px;font-weight:600;color:#6b7280;border-radius:6px;transition:all .2s}
.tab-btn.active{background:#fff;color:#302b63;box-shadow:0 1px 4px rgba(0,0,0,.1)}
.filter-bar{padding:12px 20px;border-bottom:1px solid #f3f4f6;display:flex;gap:10px;align-items:center;flex-wrap:wrap;background:#fafafa}
.filter-bar select,.filter-bar input{padding:7px 12px;border:1.5px solid #e5e7eb;border-radius:8px;font-size:13px;outline:none}
.filter-bar select:focus,.filter-bar input:focus{border-color:#302b63}
.filter-bar input{min-width:200px}
.filter-bar button{padding:7px 16px;border:none;border-radius:8px;font-size:13px;font-weight:600;cursor:pointer;transition:all .2s}
.btn-primary{background:#302b63;color:#fff}.btn-primary:hover{background:#1e1a45}
.btn-clear{background:#f3f4f6;color:#374151}.btn-clear:hover{background:#e5e7eb}
.tbl-wrap{overflow-x:auto;min-height:300px}
table{width:100%;border-collapse:collapse;font-size:12px}
thead{background:#f8f9fa;position:sticky;top:0;z-index:5}
th{padding:10px 14px;text-align:left;font-weight:600;color:#374151;border-bottom:2px solid #e5e7eb;white-space:nowrap;cursor:pointer;user-select:none}
th:hover{background:#e9ecf0}
th .sort-icon{margin-left:4px;color:#9ca3af;font-size:10px}
th.sort-asc .sort-icon::after{content:'▲';color:#302b63}
th.sort-desc .sort-icon::after{content:'▼';color:#302b63}
th:not(.sort-asc):not(.sort-desc) .sort-icon::after{content:'⇅'}
td{padding:8px 14px;border-bottom:1px solid #f3f4f6;white-space:nowrap;max-width:260px;overflow:hidden;text-overflow:ellipsis}
tr:hover{background:#f8fafc}
.pagination{padding:12px 20px;display:flex;justify-content:space-between;align-items:center;border-top:1px solid #f3f4f6;flex-wrap:wrap;gap:8px}
.page-info{font-size:13px;color:#6b7280}
.page-controls{display:flex;gap:6px;align-items:center}
.page-btn{padding:6px 12px;border:1.5px solid #e5e7eb;border-radius:8px;background:#fff;cursor:pointer;font-size:13px;transition:all .2s}
.page-btn:hover:not(:disabled){border-color:#302b63;color:#302b63}
.page-btn:disabled{opacity:.4;cursor:not-allowed}
.page-size-sel{padding:6px 10px;border:1.5px solid #e5e7eb;border-radius:8px;font-size:13px;outline:none}
.schema-wrap{padding:16px 20px}
.schema-table{width:100%;border-collapse:collapse;font-size:13px}
.schema-table th{padding:9px 14px;text-align:left;font-weight:600;color:#374151;background:#f8f9fa;border-bottom:2px solid #e5e7eb}
.schema-table td{padding:8px 14px;border-bottom:1px solid #f3f4f6;font-family:monospace;font-size:12px}
.schema-table tr:hover{background:#f8fafc}
.type-badge{display:inline-block;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:600;background:#dbeafe;color:#1e40af}
.summary-wrap{padding:16px 20px;overflow-x:auto}
.summary-table{width:100%;border-collapse:collapse;font-size:13px}
.summary-table th{padding:9px 14px;text-align:left;font-weight:600;color:#374151;background:#f8f9fa;border-bottom:2px solid #e5e7eb;white-space:nowrap}
.summary-table td{padding:8px 14px;border-bottom:1px solid #f3f4f6}
.summary-table tr:hover{background:#f8fafc}
.empty-state{padding:60px 20px;text-align:center;color:#9ca3af}
.empty-state .icon{font-size:40px;margin-bottom:12px}
.empty-state p{font-size:15px}
.empty-state .sub{font-size:13px;margin-top:4px}
.loading-row td{text-align:center;padding:40px;color:#6b7280;font-size:13px}
.toast{position:fixed;top:20px;right:20px;padding:14px 24px;border-radius:10px;color:#fff;font-size:14px;font-weight:600;z-index:2000;transform:translateX(120%);transition:transform .4s;box-shadow:0 4px 16px rgba(0,0,0,.15)}
.toast.show{transform:translateX(0)}.toast.success{background:#16a34a}.toast.error{background:#dc2626}.toast.info{background:#2563eb}
.ft{text-align:center;padding:20px;color:#9ca3af;font-size:11px}
@media(max-width:900px){.main-grid{grid-template-columns:1fr}.hd{padding:18px;flex-direction:column}}
</style>
</head>
<body>
<div class="wrap">
    <div class="hd">
        <div class="hd-top">
            <div>
                <h1>💾 DuckDB 데이터 뷰어</h1>
                <p>로컬 DuckDB에 저장된 주가 데이터를 조회합니다</p>
            </div>
            <div class="hd-nav">
                <a href="/">← 스크리닝 대시보드</a>
                <a href="/backtest">📊 백테스트</a>
            </div>
        </div>
    </div>

    <div class="sg" id="statsGrid">
        <div class="sc"><div class="n" id="statSize">-</div><div class="l">DB 크기</div></div>
        <div class="sc"><div class="n" id="statRows">-</div><div class="l">총 레코드 수</div></div>
        <div class="sc"><div class="n" id="statTickers">-</div><div class="l">종목 수</div></div>
        <div class="sc"><div class="n" id="statDates">-</div><div class="l">데이터 기간</div></div>
    </div>

    <div class="main-grid">
        <div class="sidebar">
            <h3>테이블 목록</h3>
            <div id="tableList">
                <div class="empty-state" style="padding:30px 16px">
                    <div class="icon">⏳</div>
                    <p style="font-size:13px">로딩 중...</p>
                </div>
            </div>
        </div>

        <div class="content-panel">
            <div class="panel-header">
                <h3 id="panelTitle">테이블을 선택하세요</h3>
                <div class="tab-bar" id="tabBar" style="display:none">
                    <button class="tab-btn active" onclick="switchTab('data', this)">데이터</button>
                    <button class="tab-btn" onclick="switchTab('schema', this)">스키마</button>
                    <button class="tab-btn" onclick="switchTab('summary', this)" id="summaryTabBtn">종목 요약</button>
                </div>
            </div>

            <!-- 데이터 탭 -->
            <div id="dataTab">
                <div class="filter-bar" id="filterBar" style="display:none">
                    <select id="filterCol"><option value="">-- 컬럼 선택 --</option></select>
                    <input type="text" id="filterVal" placeholder="필터 값 입력..." onkeydown="if(event.key==='Enter')applyFilter()">
                    <select id="orderDirSel">
                        <option value="DESC">내림차순</option>
                        <option value="ASC">오름차순</option>
                    </select>
                    <button class="btn-primary" onclick="applyFilter()">적용</button>
                    <button class="btn-clear" onclick="clearFilter()">초기화</button>
                </div>
                <div class="tbl-wrap">
                    <table id="dataTable">
                        <thead id="dataHead"></thead>
                        <tbody id="dataBody">
                            <tr><td colspan="99" class="loading-row" style="text-align:center;padding:60px;color:#9ca3af">
                                <div style="font-size:32px;margin-bottom:8px">🗄️</div>
                                <div style="font-size:15px">왼쪽에서 테이블을 선택하세요</div>
                            </td></tr>
                        </tbody>
                    </table>
                </div>
                <div class="pagination" id="pagination" style="display:none">
                    <div class="page-info" id="pageInfo"></div>
                    <div class="page-controls">
                        <select class="page-size-sel" id="pageSizeSel" onchange="changePageSize()">
                            <option value="20">20행</option>
                            <option value="50" selected>50행</option>
                            <option value="100">100행</option>
                            <option value="200">200행</option>
                        </select>
                        <button class="page-btn" id="btnFirst" onclick="goPage(1)">«</button>
                        <button class="page-btn" id="btnPrev" onclick="goPage(state.page - 1)">‹</button>
                        <span id="pageNumDisplay" style="font-size:13px;color:#374151;padding:0 4px"></span>
                        <button class="page-btn" id="btnNext" onclick="goPage(state.page + 1)">›</button>
                        <button class="page-btn" id="btnLast" onclick="goPage(state.totalPages)">»</button>
                    </div>
                </div>
            </div>

            <!-- 스키마 탭 -->
            <div id="schemaTab" style="display:none">
                <div class="schema-wrap">
                    <table class="schema-table">
                        <thead><tr><th>#</th><th>컬럼명</th><th>데이터 타입</th></tr></thead>
                        <tbody id="schemaBody"></tbody>
                    </table>
                </div>
            </div>

            <!-- 종목 요약 탭 -->
            <div id="summaryTab" style="display:none">
                <div class="summary-wrap">
                    <table class="summary-table">
                        <thead><tr>
                            <th>종목코드</th><th>종목명</th>
                            <th>시작일</th><th>종료일</th>
                            <th style="text-align:right">레코드</th>
                            <th style="text-align:right">최근 종가</th>
                        </tr></thead>
                        <tbody id="summaryBody"></tbody>
                    </table>
                </div>
            </div>
        </div>
    </div>

    <div class="ft">DuckDB 로컬 저장소 | 투자 참고용이며, 투자의 최종 책임은 투자자 본인에게 있습니다.</div>
</div>
<div class="toast" id="toast"></div>

<script>
const state = {
    table: null,
    page: 1,
    pageSize: 50,
    orderBy: null,
    orderDir: 'DESC',
    filterCol: null,
    filterVal: null,
    totalPages: 1,
    schema: [],
    activeTab: 'data',
};

function showToast(msg, type) {
    const t = document.getElementById('toast');
    t.textContent = msg; t.className = 'toast ' + type + ' show';
    setTimeout(() => t.classList.remove('show'), 3000);
}

function fmt(n) {
    if (n == null) return '-';
    if (typeof n === 'number') return n.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    return n;
}

// ---- 초기화 ----
window.addEventListener('DOMContentLoaded', () => {
    loadTables();
    loadTickerSummary();
});

function loadTables() {
    fetch('/api/db/tables')
        .then(r => r.json())
        .then(d => {
            renderStats(d.stats);
            renderTableList(d.tables);
        })
        .catch(e => {
            showToast('테이블 목록 로드 실패: ' + e.message, 'error');
            document.getElementById('tableList').innerHTML =
                '<div class="empty-state" style="padding:30px 16px"><p style="font-size:13px;color:#dc2626">로드 실패</p></div>';
        });
}

function renderStats(stats) {
    if (!stats) return;
    document.getElementById('statSize').textContent = (stats.db_size_mb || 0) + ' MB';
    document.getElementById('statRows').textContent = fmt(stats.total_records || 0);
    document.getElementById('statTickers').textContent = fmt(stats.total_tickers || 0);
    const minDate = stats.date_min || '-';
    const maxDate = stats.date_max || '-';
    document.getElementById('statDates').textContent = minDate === '-' ? '-' : minDate + ' ~ ' + maxDate;
}

function renderTableList(tables) {
    const el = document.getElementById('tableList');
    if (!tables || !tables.length) {
        el.innerHTML = '<div class="empty-state" style="padding:30px 16px"><div class="icon">📭</div><p style="font-size:13px">테이블 없음</p></div>';
        return;
    }
    el.innerHTML = tables.map(t => `
        <div class="tbl-item" id="titem_${t.table_name}" onclick="selectTable('${t.table_name}')">
            <span class="tbl-name">${t.table_name}</span>
            <span class="row-cnt">${fmt(t.row_count)}</span>
        </div>
    `).join('');
}

// ---- 테이블 선택 ----
function selectTable(name) {
    if (state.table === name) return;

    // 사이드바 하이라이트
    document.querySelectorAll('.tbl-item').forEach(el => el.classList.remove('active'));
    const item = document.getElementById('titem_' + name);
    if (item) item.classList.add('active');

    state.table = name;
    state.page = 1;
    state.orderBy = null;
    state.orderDir = 'DESC';
    state.filterCol = null;
    state.filterVal = null;

    document.getElementById('panelTitle').textContent = '📋 ' + name;
    document.getElementById('tabBar').style.display = '';
    document.getElementById('filterBar').style.display = '';
    document.getElementById('pagination').style.display = '';

    // 필터 초기화 UI
    document.getElementById('filterVal').value = '';
    document.getElementById('orderDirSel').value = 'DESC';

    // 스키마 로드 → 필터 컬럼 채우기 → 데이터 로드
    loadSchema(name).then(() => {
        loadData();
    });

    // 활성 탭이 data 아니면 data로 전환
    switchTab('data', document.querySelector('.tab-btn'));
}

function loadSchema(name) {
    return fetch('/api/db/schema/' + name)
        .then(r => r.json())
        .then(d => {
            state.schema = d.schema || [];
            renderSchema(state.schema);
            populateFilterCols(state.schema);
        })
        .catch(e => {
            showToast('스키마 로드 실패: ' + e.message, 'error');
        });
}

function renderSchema(schema) {
    const body = document.getElementById('schemaBody');
    if (!schema || !schema.length) {
        body.innerHTML = '<tr><td colspan="3" style="text-align:center;padding:20px;color:#9ca3af">스키마 정보 없음</td></tr>';
        return;
    }
    body.innerHTML = schema.map((col, i) => `
        <tr>
            <td style="color:#9ca3af">${i + 1}</td>
            <td><b>${col.column_name}</b></td>
            <td><span class="type-badge">${col.column_type}</span></td>
        </tr>
    `).join('');
}

function populateFilterCols(schema) {
    const sel = document.getElementById('filterCol');
    sel.innerHTML = '<option value="">-- 컬럼 선택 --</option>' +
        (schema || []).map(col => `<option value="${col.column_name}">${col.column_name}</option>`).join('');
}

// ---- 데이터 로드 ----
function loadData() {
    if (!state.table) return;

    // 로딩 표시
    document.getElementById('dataBody').innerHTML =
        '<tr class="loading-row"><td colspan="99">⏳ 데이터 로딩 중...</td></tr>';

    const params = new URLSearchParams({
        page: state.page,
        page_size: state.pageSize,
        order_dir: state.orderDir,
    });
    if (state.orderBy) params.set('order_by', state.orderBy);
    if (state.filterCol && state.filterVal) {
        params.set('filter_col', state.filterCol);
        params.set('filter_val', state.filterVal);
    }

    fetch('/api/db/query/' + state.table + '?' + params.toString())
        .then(r => {
            if (!r.ok) return r.json().then(e => { throw new Error(e.error || r.status); });
            return r.json();
        })
        .then(d => {
            state.totalPages = d.total_pages || 1;
            state.page = d.page || 1;
            renderDataTable(d.rows, d.total, d.page, d.page_size, d.total_pages);
        })
        .catch(e => {
            showToast('데이터 로드 실패: ' + e.message, 'error');
            document.getElementById('dataBody').innerHTML =
                '<tr class="loading-row"><td colspan="99" style="color:#dc2626">오류: ' + e.message + '</td></tr>';
        });
}

function renderDataTable(rows, total, page, pageSize, totalPages) {
    const head = document.getElementById('dataHead');
    const body = document.getElementById('dataBody');

    if (!rows || !rows.length) {
        head.innerHTML = '';
        body.innerHTML = '<tr><td colspan="99" class="loading-row"><div style="font-size:28px;margin-bottom:8px">📭</div><div>데이터가 없습니다</div></td></tr>';
        document.getElementById('pageInfo').textContent = '0건';
        updatePageButtons(1, 1);
        return;
    }

    const cols = Object.keys(rows[0]);

    // 헤더 (정렬 아이콘 포함)
    head.innerHTML = '<tr>' + cols.map(col => {
        const isActive = state.orderBy === col;
        const cls = isActive ? ('sort-' + state.orderDir.toLowerCase()) : '';
        return `<th class="${cls}" onclick="sortBy('${col}')">
            ${col}<span class="sort-icon"></span>
        </th>`;
    }).join('') + '</tr>';

    // 바디
    body.innerHTML = rows.map(row =>
        '<tr>' + cols.map(col => {
            const v = row[col];
            const display = v == null ? '<span style="color:#d1d5db">NULL</span>' :
                (typeof v === 'number' ? fmt(v) : String(v));
            return `<td title="${v != null ? String(v).replace(/"/g, '&quot;') : ''}">${display}</td>`;
        }).join('') + '</tr>'
    ).join('');

    // 페이지 정보
    const from = (page - 1) * pageSize + 1;
    const to = Math.min(page * pageSize, total);
    document.getElementById('pageInfo').textContent =
        `${fmt(total)}건 중 ${fmt(from)}–${fmt(to)}건 (${page} / ${totalPages} 페이지)`;
    document.getElementById('pageNumDisplay').textContent = page + ' / ' + totalPages;
    updatePageButtons(page, totalPages);
}

function updatePageButtons(page, totalPages) {
    document.getElementById('btnFirst').disabled = page <= 1;
    document.getElementById('btnPrev').disabled = page <= 1;
    document.getElementById('btnNext').disabled = page >= totalPages;
    document.getElementById('btnLast').disabled = page >= totalPages;
}

// ---- 페이지 이동 ----
function goPage(p) {
    p = Math.max(1, Math.min(p, state.totalPages));
    if (p === state.page) return;
    state.page = p;
    loadData();
}

function changePageSize() {
    state.pageSize = +document.getElementById('pageSizeSel').value;
    state.page = 1;
    loadData();
}

// ---- 정렬 ----
function sortBy(col) {
    if (state.orderBy === col) {
        state.orderDir = state.orderDir === 'DESC' ? 'ASC' : 'DESC';
    } else {
        state.orderBy = col;
        state.orderDir = 'DESC';
    }
    document.getElementById('orderDirSel').value = state.orderDir;
    state.page = 1;
    loadData();
}

// ---- 필터 ----
function applyFilter() {
    const col = document.getElementById('filterCol').value;
    const val = document.getElementById('filterVal').value.trim();
    const dir = document.getElementById('orderDirSel').value;
    state.filterCol = col || null;
    state.filterVal = val || null;
    state.orderDir = dir;
    state.page = 1;
    loadData();
}

function clearFilter() {
    document.getElementById('filterCol').value = '';
    document.getElementById('filterVal').value = '';
    document.getElementById('orderDirSel').value = 'DESC';
    state.filterCol = null;
    state.filterVal = null;
    state.orderDir = 'DESC';
    state.page = 1;
    loadData();
}

// ---- 탭 전환 ----
function switchTab(tab, btn) {
    state.activeTab = tab;
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    if (btn) btn.classList.add('active');

    document.getElementById('dataTab').style.display = tab === 'data' ? '' : 'none';
    document.getElementById('schemaTab').style.display = tab === 'schema' ? '' : 'none';
    document.getElementById('summaryTab').style.display = tab === 'summary' ? '' : 'none';
    document.getElementById('filterBar').style.display = tab === 'data' ? '' : 'none';
    document.getElementById('pagination').style.display = tab === 'data' ? '' : 'none';
}

// ---- 종목 요약 ----
function loadTickerSummary() {
    fetch('/api/db/ticker-summary')
        .then(r => r.json())
        .then(d => renderTickerSummary(d.summary || []))
        .catch(() => {});
}

function renderTickerSummary(summary) {
    const body = document.getElementById('summaryBody');
    if (!summary.length) {
        body.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:30px;color:#9ca3af">종목 데이터 없음</td></tr>';
        return;
    }
    body.innerHTML = summary.map(s => `
        <tr>
            <td style="font-family:monospace;color:#6b7280">${s.ticker}</td>
            <td><b>${s.name || '-'}</b></td>
            <td>${s.min_date || '-'}</td>
            <td>${s.max_date || '-'}</td>
            <td style="text-align:right">${fmt(s.count)}</td>
            <td style="text-align:right">${s.latest_close != null ? fmt(s.latest_close) + '원' : '-'}</td>
        </tr>
    `).join('');
}
</script>
</body>
</html>'''


# ============================================================
# 스케줄러 설정
# ============================================================
scheduler = BackgroundScheduler()
scheduler.add_job(refresh_data, 'cron', hour=8, minute=0, id='daily_refresh')


# ============================================================
# 메인
# ============================================================
if __name__ == '__main__':
    logger.info("=" * 50)
    logger.info("  한국 증시 종합 스크리닝 시스템 시작")
    logger.info("=" * 50)

    # 캐시 로드 시도
    if not load_cache():
        logger.info("캐시 없음. 초기 데이터 수집 시작...")
        refresh_data()

    # 스케줄러 시작 (매일 아침 8시)
    scheduler.start()
    logger.info("스케줄러 등록: 매일 08:00 자동 갱신")

    # 다음 실행 시간 표시
    job = scheduler.get_job('daily_refresh')
    if job and job.next_run_time:
        logger.info(f"다음 자동 갱신: {job.next_run_time.strftime('%Y-%m-%d %H:%M:%S')}")

    logger.info("서버 시작: http://localhost:5000")
    logger.info("=" * 50)

    try:
        app.run(host='127.0.0.1', port=5000, debug=False)
    finally:
        scheduler.shutdown()
