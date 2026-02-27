#!/usr/bin/env python3
"""
일일 자동 리포트 스크립트 (GitHub Actions 전용)
================================================
1. FnGuide 3개 지표 크롤링 → 종목 스코어링
2. 2점 이상 종목 대상 백테스트 (슬리피지/수수료/세금 반영)
3. KOSPI 벤치마크 대비 성과 비교
4. 상위 10종목 텔레그램 전송 + CSV 저장

환경변수:
    TELEGRAM_BOT_TOKEN  - 텔레그램 봇 토큰
    TELEGRAM_CHAT_ID    - 텔레그램 채팅 ID
"""

import csv
import io
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timedelta

import requests
import yfinance as yf

from backtester import BacktestEngine

# app.py에서 크롤링/스코어링 함수 가져오기
# (scheduler.start()는 __main__ 블록 안에 있으므로 안전)
from app import fetch_all_data, calculate_scores

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
)
logger = logging.getLogger(__name__)

# ============================================================
# 설정
# ============================================================
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')

# 백테스트 기본 파라미터
BACKTEST_PERIOD_MONTHS = 6
INITIAL_CAPITAL = 100_000_000  # 1억원
STRATEGY = 'composite'         # 복합 전략 (MA + 변동성 + 스탑)
SLIPPAGE_PCT = 0.3             # 슬리피지 0.3%
COMMISSION_PCT = 0.015         # 수수료 0.015% (매수/매도 각각)
TAX_PCT = 0.20                 # 증권거래세 0.20% (매도시)

OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__))


# ============================================================
# 텔레그램 전송
# ============================================================
def send_telegram(text: str) -> bool:
    """텔레그램 메시지 전송 (MarkdownV2)"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("텔레그램 설정 없음 (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID)")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML',
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            logger.info("텔레그램 전송 성공")
            return True
        else:
            logger.error(f"텔레그램 전송 실패: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logger.error(f"텔레그램 전송 오류: {e}")
        return False


def send_telegram_document(file_path: str, caption: str = '') -> bool:
    """텔레그램 파일(CSV) 전송"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        with open(file_path, 'rb') as f:
            resp = requests.post(
                url,
                data={'chat_id': TELEGRAM_CHAT_ID, 'caption': caption},
                files={'document': (os.path.basename(file_path), f)},
                timeout=60,
            )
        if resp.status_code == 200:
            logger.info("텔레그램 CSV 전송 성공")
            return True
        else:
            logger.error(f"텔레그램 CSV 전송 실패: {resp.status_code} {resp.text}")
            return False
    except Exception as e:
        logger.error(f"텔레그램 CSV 전송 오류: {e}")
        return False


# ============================================================
# CSV 생성
# ============================================================
def generate_csv(engine: BacktestEngine, results: dict) -> str:
    """백테스트 결과를 CSV 파일로 저장하고 경로 반환"""
    daily_rows = engine.get_daily_detail()

    output = io.StringIO()
    output.write('\ufeff')  # BOM for Excel 한글 호환

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

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'backtest_{STRATEGY}_{timestamp}.csv'
    filepath = os.path.join(OUTPUT_DIR, filename)

    with open(filepath, 'w', encoding='utf-8-sig') as f:
        f.write(csv_data)

    logger.info(f"CSV 저장: {filepath}")
    return filepath


# ============================================================
# 텔레그램 메시지 포맷팅
# ============================================================
def format_telegram_message(scored_results: list, stats: dict,
                            bt_results: dict, cost_summary: dict) -> str:
    """텔레그램 메시지 HTML 포맷"""
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    metrics = bt_results.get('metrics', {})
    benchmark = bt_results.get('benchmark')
    config = bt_results.get('cost_config', {})

    lines = []
    lines.append(f"<b>📊 국장검색 일일 리포트</b>")
    lines.append(f"<i>{now}</i>")
    lines.append("")

    # 스크리닝 요약
    lines.append("<b>▸ 스크리닝 요약</b>")
    lines.append(f"  연간실적호전: {stats.get('turn_count', 0)}종목")
    lines.append(f"  순매수전환: {stats.get('supply_count', 0)}종목")
    lines.append(f"  국민연금: {stats.get('nps_count', 0)}종목")
    lines.append(f"  3점: {stats.get('score_3', 0)} | 2점: {stats.get('score_2', 0)} | 1점: {stats.get('score_1', 0)}")
    lines.append("")

    # 상위 10종목
    lines.append("<b>▸ 상위 10종목</b>")
    top10 = scored_results[:10]
    for i, stock in enumerate(top10):
        name = stock['종목명']
        score = stock['종합점수']
        sources = stock.get('출처', '')
        medal = ['🥇', '🥈', '🥉'][i] if i < 3 else f"{i+1}."
        lines.append(f"  {medal} <b>{name}</b> ({score}점) - {sources}")
    lines.append("")

    # 백테스트 결과
    strategy_names = {
        'equal_weight': '동일 비중 Buy & Hold',
        'rebalance': '월간 리밸런싱',
        'vol_trailing_stop': '변동성 + 트레일링 스탑',
        'ma_filter': 'MA 필터',
        'composite': '복합 전략 (MA+변동성+스탑)',
    }
    lines.append("<b>▸ 백테스트 결과</b>")
    lines.append(f"  전략: {strategy_names.get(STRATEGY, STRATEGY)}")
    lines.append(f"  기간: {metrics.get('start_date', '')} ~ {metrics.get('end_date', '')}")
    lines.append(f"  초기자본: {INITIAL_CAPITAL:,.0f}원")
    lines.append(f"  최종자산: {metrics.get('final_equity', 0):,.0f}원")
    lines.append(f"  수익률: {metrics.get('total_return', 0):+.2f}%")
    lines.append(f"  연환산수익률: {metrics.get('annual_return', 0):+.2f}%")
    lines.append(f"  MDD: {metrics.get('mdd', 0):.2f}%")
    lines.append(f"  샤프비율: {metrics.get('sharpe', 0):.2f}")
    lines.append(f"  승률: {metrics.get('win_rate', 0):.1f}%")
    lines.append(f"  총 거래: {metrics.get('total_trades', 0)}건")
    lines.append("")

    # 비용 요약
    lines.append("<b>▸ 거래비용 반영</b>")
    lines.append(f"  슬리피지: {config.get('slippage_pct', SLIPPAGE_PCT)}% → {cost_summary.get('slippage', 0):,.0f}원")
    lines.append(f"  수수료: {config.get('commission_pct', COMMISSION_PCT)}% → {cost_summary.get('commission', 0):,.0f}원")
    lines.append(f"  거래세: {config.get('tax_pct', TAX_PCT)}% → {cost_summary.get('tax', 0):,.0f}원")
    lines.append(f"  <b>총 비용: {cost_summary.get('total', 0):,.0f}원</b>")
    lines.append("")

    # KOSPI 벤치마크 비교
    if benchmark:
        bm_return = benchmark.get('return_pct', 0)
        bm_mdd = benchmark.get('mdd', 0)
        port_return = metrics.get('total_return', 0)
        alpha = port_return - bm_return
        lines.append("<b>▸ KOSPI 벤치마크 비교</b>")
        lines.append(f"  포트폴리오: {port_return:+.2f}%")
        lines.append(f"  KOSPI: {bm_return:+.2f}%")
        lines.append(f"  초과수익(α): {alpha:+.2f}%")
        lines.append(f"  KOSPI MDD: {bm_mdd:.2f}%")
        if alpha > 0:
            lines.append("  ✅ KOSPI 대비 초과 수익 달성")
        else:
            lines.append("  ⚠️ KOSPI 대비 언더퍼폼")
    else:
        lines.append("<i>KOSPI 벤치마크 데이터 없음</i>")

    # 개별 종목 성과
    stock_perf = bt_results.get('stock_performance', [])
    if stock_perf:
        lines.append("")
        lines.append("<b>▸ 개별 종목 수익률</b>")
        sorted_perf = sorted(stock_perf, key=lambda x: x.get('return_pct', 0), reverse=True)
        for sp in sorted_perf[:10]:
            ret = sp.get('return_pct', 0)
            icon = "📈" if ret >= 0 else "📉"
            lines.append(f"  {icon} {sp['name']}: {ret:+.2f}% (MDD {sp.get('mdd', 0):.1f}%)")

    return "\n".join(lines)


# ============================================================
# 메인 파이프라인
# ============================================================
def main():
    logger.info("=" * 60)
    logger.info("  일일 자동 리포트 시작")
    logger.info("=" * 60)

    # ── 1단계: FnGuide 크롤링 ──
    logger.info("[1/5] FnGuide 3개 지표 크롤링 시작...")
    turn_data, supply_data, nps_data = fetch_all_data()

    if not turn_data and not supply_data and not nps_data:
        msg = "모든 데이터 소스에서 수집 실패"
        logger.error(msg)
        send_telegram(f"❌ <b>국장검색 리포트 실패</b>\n{msg}")
        sys.exit(1)

    logger.info(f"  턴어라운드: {len(turn_data)}개 | 순매수전환: {len(supply_data)}개 | 국민연금: {len(nps_data)}개")

    # ── 2단계: 스코어링 ──
    logger.info("[2/5] 종목 스코어링...")
    scored_results, stats = calculate_scores(turn_data, supply_data, nps_data)
    logger.info(f"  3점: {stats['score_3']} | 2점: {stats['score_2']} | 1점: {stats['score_1']}")

    high_score = [r for r in scored_results if r.get('종합점수', 0) >= 2]
    if not high_score:
        msg = "2점 이상 종목이 없습니다"
        logger.warning(msg)
        send_telegram(f"⚠️ <b>국장검색 리포트</b>\n{msg}\n\n1점 종목: {stats['score_1']}개")
        sys.exit(0)

    stock_names = [r['종목명'] for r in high_score]
    logger.info(f"  백테스트 대상: {len(stock_names)}개 종목")

    # ── 3단계: 종목코드 매핑 + 가격 데이터 수집 (yfinance) ──
    logger.info("[3/5] 종목코드 매핑 및 가격 데이터 수집...")

    # ticker_map.json 로드 (pykrx 대신 - KRX API는 해외 IP 차단)
    ticker_map_path = os.path.join(OUTPUT_DIR, 'ticker_map.json')
    if not os.path.exists(ticker_map_path):
        msg = "ticker_map.json 파일이 없습니다. 로컬에서 생성 후 커밋하세요."
        logger.error(msg)
        send_telegram(f"❌ <b>국장검색 리포트 실패</b>\n{msg}")
        sys.exit(1)

    with open(ticker_map_path, 'r', encoding='utf-8') as f:
        name_to_code = json.load(f)
    logger.info(f"  ticker_map.json 로드: {len(name_to_code)}개 종목")

    matched = {}
    unmatched = []
    for name in stock_names:
        code = name_to_code.get(name)
        if code:
            matched[code] = name
        else:
            unmatched.append(name)

    if not matched:
        msg = f"종목코드 매핑 실패: {', '.join(stock_names[:5])}"
        logger.error(msg)
        send_telegram(f"❌ <b>국장검색 리포트 실패</b>\n{msg}")
        sys.exit(1)

    if unmatched:
        logger.warning(f"코드 매핑 실패: {', '.join(unmatched)}")

    logger.info(f"  매핑 성공: {len(matched)}개 | 실패: {len(unmatched)}개")

    # 기간 설정
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=BACKTEST_PERIOD_MONTHS * 30)
    start_iso = start_dt.strftime('%Y-%m-%d')
    end_iso = end_dt.strftime('%Y-%m-%d')

    # yfinance로 가격 데이터 수집 (KRX API 해외 차단 우회)
    # KOSPI: 종목코드 + ".KS", KOSDAQ: 종목코드 + ".KQ"
    logger.info(f"  yfinance로 가격 데이터 수집 중... ({len(matched)}종목)")

    def get_yf_ticker(code: str) -> str:
        """종목코드를 yfinance 심볼로 변환 (KOSPI=.KS, KOSDAQ=.KQ)"""
        # 6자리 숫자 코드 → .KS 시도 후 실패시 .KQ
        return f"{code}.KS"

    # ── 4단계: 백테스트 실행 ──
    logger.info("[4/5] 백테스트 실행...")
    engine = BacktestEngine(
        initial_capital=INITIAL_CAPITAL,
        slippage_pct=SLIPPAGE_PCT,
        commission_pct=COMMISSION_PCT,
        tax_pct=TAX_PCT,
    )

    failed_tickers = []
    for i, (code, name) in enumerate(matched.items()):
        yf_symbol = get_yf_ticker(code)
        try:
            df = yf.download(yf_symbol, start=start_iso, end=end_iso,
                             progress=False, auto_adjust=True)
            if df.empty:
                # KOSPI 실패 → KOSDAQ 시도
                yf_symbol = f"{code}.KQ"
                df = yf.download(yf_symbol, start=start_iso, end=end_iso,
                                 progress=False, auto_adjust=True)

            if not df.empty:
                # yfinance DataFrame → BacktestEngine 형식 변환
                # MultiIndex columns 처리 (yfinance 0.2.31+)
                if isinstance(df.columns, __import__('pandas').MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                prices = []
                for date_idx, row in df.iterrows():
                    prices.append({
                        'date': date_idx.strftime('%Y-%m-%d'),
                        'open': float(row['Open']),
                        'high': float(row['High']),
                        'low': float(row['Low']),
                        'close': float(row['Close']),
                        'volume': int(row['Volume']),
                    })
                engine.add_price_data(code, prices, name=name)
                logger.info(f"  [{i+1}/{len(matched)}] {name}({yf_symbol}): {len(prices)}일")
            else:
                failed_tickers.append(name)
                logger.warning(f"  [{i+1}/{len(matched)}] {name}({yf_symbol}): 데이터 없음")
        except Exception as e:
            failed_tickers.append(name)
            logger.warning(f"  [{i+1}/{len(matched)}] {name}({code}): 오류 - {e}")

    if failed_tickers:
        logger.warning(f"  가격 수집 실패: {', '.join(failed_tickers)}")

    if not engine.price_data:
        msg = "가격 데이터를 수집한 종목이 없습니다"
        logger.error(msg)
        send_telegram(f"❌ <b>국장검색 리포트 실패</b>\n{msg}")
        sys.exit(1)

    # KOSPI 벤치마크 (yfinance: ^KS11)
    try:
        kospi_df = yf.download("^KS11", start=start_iso, end=end_iso,
                               progress=False, auto_adjust=True)
        if isinstance(kospi_df.columns, __import__('pandas').MultiIndex):
            kospi_df.columns = kospi_df.columns.get_level_values(0)
        if not kospi_df.empty:
            kospi = [{'date': d.strftime('%Y-%m-%d'), 'close': float(r['Close'])}
                     for d, r in kospi_df.iterrows()]
            engine.set_benchmark(kospi)
            logger.info(f"  KOSPI 벤치마크: {len(kospi)}일")
    except Exception as e:
        logger.warning(f"  KOSPI 벤치마크 수집 실패: {e}")

    # 전략 실행
    tickers = list(engine.price_data.keys())
    if STRATEGY == 'rebalance':
        engine.run_rebalance(tickers, period=20)
    elif STRATEGY == 'vol_trailing_stop':
        engine.run_volatility_trailing_stop(
            tickers, lookback=20, stop_pct=-10.0, cooldown=5, reentry=True)
    elif STRATEGY == 'ma_filter':
        engine.run_ma_filter(tickers, ma_period=20, rebalance_period=5)
    elif STRATEGY == 'composite':
        engine.run_composite(
            tickers, ma_period=20, lookback=20,
            stop_pct=-8.0, cooldown=5, rebalance_period=10)
    else:
        engine.run_equal_weight(tickers)

    bt_results = engine.get_results()
    cost_summary = bt_results.get('cost_summary', {})
    metrics = bt_results.get('metrics', {})

    logger.info(f"  수익률: {metrics.get('total_return', 0):+.2f}%")
    logger.info(f"  MDD: {metrics.get('mdd', 0):.2f}%")
    logger.info(f"  총 비용: {cost_summary.get('total', 0):,.0f}원")

    # ── 5단계: 결과 전송 ──
    logger.info("[5/5] 결과 전송...")

    # CSV 생성
    csv_path = generate_csv(engine, bt_results)

    # 텔레그램 메시지 전송
    message = format_telegram_message(scored_results, stats, bt_results, cost_summary)
    send_telegram(message)

    # 텔레그램 CSV 전송
    caption = f"백테스트 CSV ({datetime.now().strftime('%Y-%m-%d')})"
    send_telegram_document(csv_path, caption=caption)

    logger.info("=" * 60)
    logger.info("  일일 자동 리포트 완료")
    logger.info(f"  CSV: {csv_path}")
    logger.info("=" * 60)


if __name__ == '__main__':
    main()
