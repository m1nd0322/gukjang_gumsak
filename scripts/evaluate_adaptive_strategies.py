#!/usr/bin/env python3
"""Reproducible, cost-inclusive ETF evaluation; no parameter fitting."""
import argparse
import json
import sys
from bisect import bisect_left
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtester import BacktestEngine
from stock_db import StockDB
from strategy_catalog import ETF_ASSETS, ETF_STRATEGY_KEYS


def evaluate(prices, benchmark):
    if not benchmark:
        raise ValueError('KOSPI benchmark is unavailable')
    available = {t: {r['date'] for r in rows} for t, rows in prices.items()}
    common = sorted(set.intersection(*available.values()) & {r['date'] for r in benchmark})
    if len(common) < 253 + 756:
        raise ValueError('At least 253 warmup and 756 evaluation sessions are required')
    start, end = common[253], common[-1]
    oos_start = common[253 + int((len(common) - 253) * .7)]
    names = {a.ticker: a.name for a in ETF_ASSETS.values()}

    def run(key, first, last):
        engine = BacktestEngine(initial_capital=100_000_000, commission_pct=.015,
                                slippage_pct=.10, tax_pct=0)
        for ticker, rows in prices.items():
            engine.add_price_data(ticker, rows, names[ticker])
        engine.set_benchmark(benchmark)
        engine.run_adaptive_strategy(key, first, last)
        return engine.get_results()

    report = dict(status='experimental', start=start, end=end, out_of_sample_start=oos_start,
                  source='yfinance_auto_adjust', tax_model='etf_pre_tax',
                  costs=dict(commission_pct=.015, slippage_pct=.10, tax_pct=0),
                  strategy_version='1.0', parameters_adjusted=True,
                  adjustment='Single shared risk/real-asset budget reduction to 0.5',
                  out_of_sample_note='Chronological holdout; shared budget was adjusted once after aggregate evaluation, so this is not an untouched final holdout.',
                  strategies={})
    for key in sorted(ETF_STRATEGY_KEYS):
        result = run(key, start, end)
        oos = run(key, oos_start, end)
        curve = result['equity_curve']
        curve_dates = [r['date'] for r in curve]
        rolling = []
        for row in curve:
            d = date.fromisoformat(row['date'])
            try:
                target = d.replace(year=d.year + 3).isoformat()
            except ValueError:
                target = d.replace(year=d.year + 3, day=28).isoformat()
            i = bisect_left(curve_dates, target)
            if i < len(curve):
                rolling.append(curve[i]['equity'] / row['equity'] - 1)
        ratio = sum(value > 0 for value in rolling) / len(rolling) if rolling else 0
        metrics = result['metrics']
        passed = (metrics['annual_return'] > 0
                  and abs(metrics['mdd']) < abs(result['benchmark']['mdd'])
                  and ratio >= .75 and abs(metrics['mdd']) <= 11.5)
        shocks = {}
        for label, first, last in [('2020', '2020-01-01', '2020-12-31'),
                                   ('2022', '2022-01-01', '2022-12-31'),
                                   ('recent', '2026-01-01', end)]:
            first, last = max(first, start), min(last, end)
            if first <= last:
                shocks[label] = run(key, first, last)['metrics']
        report['strategies'][key] = dict(
            passed=passed, metrics=metrics, benchmark=result['benchmark']['mdd'],
            failure_reasons=[label for label, ok in (
                ('nonpositive_cagr', metrics['annual_return'] > 0),
                ('mdd_not_better_than_kospi', abs(metrics['mdd']) < abs(result['benchmark']['mdd'])),
                ('rolling_positive_ratio_below_75pct', ratio >= .75),
                ('mdd_overshoot_above_1_5pp', abs(metrics['mdd']) <= 11.5)) if not ok],
            out_of_sample=oos['metrics'], positive_rolling_3y_ratio=ratio,
            rolling_windows=len(rolling), max_drawdown_overshoot=result['max_drawdown_overshoot'],
            shocks=shocks, guard_events=result['drawdown_guard_events'],
            data_quality=result['data_quality'])
    report['passed_count'] = sum(r['passed'] for r in report['strategies'].values())
    report['status'] = 'passed' if report['passed_count'] >= 2 else 'experimental'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='reports/etf_evaluation.duckdb')
    parser.add_argument('--output', default='reports/adaptive_evaluation.json')
    parser.add_argument('--end', default=date.today().isoformat())
    parser.add_argument('--cached', action='store_true', help='Evaluate the already downloaded snapshot')
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        db = StockDB(args.db)
        names = {a.ticker: a.name for a in ETF_ASSETS.values()}
        if not args.cached:
            db.ensure_adjusted_etf_data(list(names), '2010-01-01', args.end, names)
        prices = db.get_adjusted_prices_many(list(names), '2010-01-01', args.end)
        benchmark_file = output.parent / 'adaptive_benchmark.json'
        if args.cached and benchmark_file.exists():
            benchmark = json.loads(benchmark_file.read_text())
        else:
            benchmark = db._fetch_yfinance_index('1001', '2010-01-01', args.end)
            benchmark_file.write_text(json.dumps(benchmark))
        report = evaluate(prices, benchmark)
    except Exception as exc:
        report = dict(status='blocked', error=str(exc), source='yfinance_auto_adjust',
                      requested_end=args.end)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'strategies'}, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
