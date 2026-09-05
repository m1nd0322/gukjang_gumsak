#!/usr/bin/env python3
"""Reproducible, cost-inclusive ETF evaluation; no parameter fitting."""
import argparse
import json
import sys
from bisect import bisect_left
from datetime import date, datetime, timezone
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
    split = 253 + int((len(common) - 253) * .7)
    train_end, oos_start = common[split - 1], common[split]
    names = {a.ticker: a.name for a in ETF_ASSETS.values()}

    def run(key, first, last, base_risk_budget):
        engine = BacktestEngine(initial_capital=100_000_000, commission_pct=.015,
                                slippage_pct=.10, tax_pct=0)
        for ticker, rows in prices.items():
            engine.add_price_data(ticker, rows, names[ticker])
        engine.set_benchmark(benchmark)
        engine.run_adaptive_strategy(key, first, last,
                                     base_risk_budget=base_risk_budget)
        return engine.get_results()

    def rolling_positive_ratio(result):
        curve = result['equity_curve']
        curve_dates = [row['date'] for row in curve]
        rolling = []
        for row in curve:
            observed = date.fromisoformat(row['date'])
            try:
                target = observed.replace(year=observed.year + 3).isoformat()
            except ValueError:
                target = observed.replace(year=observed.year + 3, day=28).isoformat()
            i = bisect_left(curve_dates, target)
            if i < len(curve):
                rolling.append(curve[i]['equity'] > row['equity'])
        return (sum(rolling) / len(rolling) if rolling else None), len(rolling)

    def passes(result, ratio):
        metrics = result['metrics']
        benchmark_mdd = result.get('benchmark', {}).get('mdd', 0)
        return (metrics['annual_return'] > 0
                and abs(metrics['mdd']) < abs(benchmark_mdd)
                and ratio is not None and ratio >= .75
                and abs(metrics['mdd']) <= 11.5)

    candidate_budgets = (1.0, .5)
    budget_scores = {}
    for budget in candidate_budgets:
        budget_scores[str(budget)] = 0
        for key in sorted(ETF_STRATEGY_KEYS):
            training = run(key, start, train_end, budget)
            ratio, _ = rolling_positive_ratio(training)
            budget_scores[str(budget)] += int(passes(training, ratio))
    selected_budget = max(
        candidate_budgets,
        key=lambda budget: (budget_scores[str(budget)], -budget),
    )

    report = {'status': 'experimental', 'start': start, 'end': end,
                  'in_sample_end': train_end, 'out_of_sample_start': oos_start,
                  'source': 'yfinance_auto_adjust', 'tax_model': 'etf_pre_tax',
                  'costs': {'commission_pct': .015, 'slippage_pct': .10, 'tax_pct': 0},
                  'strategy_version': '1.0', 'parameters_adjusted': selected_budget != 1.0,
                  'adjustment': 'Shared base risk budget selected using in-sample data only',
                  'selected_base_risk_budget': selected_budget,
                  'in_sample_budget_scores': budget_scores,
                  'out_of_sample_note': 'Chronological holdout; the shared risk budget was selected from the in-sample period only and then frozen before the holdout.',
                  'strategies': {}}
    for key in sorted(ETF_STRATEGY_KEYS):
        result = run(key, start, end, selected_budget)
        oos = run(key, oos_start, end, selected_budget)
        ratio, rolling_windows = rolling_positive_ratio(result)
        oos_ratio, oos_rolling_windows = rolling_positive_ratio(oos)
        metrics = result['metrics']
        passed = passes(result, ratio)
        oos_passed = (oos['metrics']['annual_return'] > 0
                      and abs(oos['metrics']['mdd']) < abs(oos['benchmark']['mdd']))
        shocks = {}
        for label, first, last in [('2020', '2020-01-01', '2020-12-31'),
                                   ('2022', '2022-01-01', '2022-12-31'),
                                   ('recent', '2026-01-01', end)]:
            first, last = max(first, start), min(last, end)
            if first <= last:
                shocks[label] = run(key, first, last, selected_budget)['metrics']
        report['strategies'][key] = {
            'passed': passed, 'metrics': metrics, 'benchmark': result['benchmark']['mdd'],
            'failure_reasons': [label for label, ok in (
                ('nonpositive_cagr', metrics['annual_return'] > 0),
                ('mdd_not_better_than_kospi', abs(metrics['mdd']) < abs(result['benchmark']['mdd'])),
                ('rolling_positive_ratio_below_75pct', ratio is not None and ratio >= .75),
                ('mdd_overshoot_above_1_5pp', abs(metrics['mdd']) <= 11.5)) if not ok],
            'out_of_sample': oos['metrics'], 'out_of_sample_passed': oos_passed,
            'out_of_sample_positive_rolling_3y_ratio': oos_ratio,
            'out_of_sample_rolling_windows': oos_rolling_windows,
            'positive_rolling_3y_ratio': ratio, 'rolling_windows': rolling_windows,
            'max_drawdown_overshoot': result['max_drawdown_overshoot'],
            'shocks': shocks, 'guard_events': result['drawdown_guard_events'],
            'data_quality': result['data_quality']}
    report['passed_count'] = sum(r['passed'] for r in report['strategies'].values())
    report['out_of_sample_passed_count'] = sum(
        r['out_of_sample_passed'] for r in report['strategies'].values()
    )
    report['status'] = 'passed' if report['passed_count'] >= 2 else 'experimental'
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default='reports/etf_evaluation.duckdb')
    parser.add_argument('--output', default='reports/adaptive_evaluation.json')
    parser.add_argument('--end', default=datetime.now(timezone.utc).date().isoformat())
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
    except Exception as exc:  # noqa: BLE001
        report = {'status': 'blocked', 'error': str(exc), 'source': 'yfinance_auto_adjust',
                      'requested_end': args.end}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'strategies'}, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
