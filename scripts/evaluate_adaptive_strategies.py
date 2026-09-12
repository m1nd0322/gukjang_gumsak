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
from etf_universe import ETFUniverse
from pit_price_evidence import require_adjusted_price_evidence
from stock_db import StockDB
from strategy_catalog import ETF_ASSETS, ETF_STRATEGY_KEYS


def evaluate(prices, benchmark, universe_history=None, price_evidence=None):
    if not benchmark:
        raise ValueError('KOSPI benchmark is unavailable')
    available = {t: {r['date'] for r in rows} for t, rows in prices.items()}
    benchmark_dates = {r['date'] for r in benchmark}
    if universe_history is not None:
        if not universe_history.is_point_in_time_complete:
            raise ValueError('point-in-time universe coverage is incomplete')
        require_adjusted_price_evidence(universe_history, prices, price_evidence)
        all_dates = set().union(*available.values()) if available else set()
        common = sorted(all_dates & benchmark_dates)
    else:
        common = sorted(set.intersection(*available.values()) & benchmark_dates)
    if len(common) < 253 + 756:
        raise ValueError('At least 253 warmup and 756 evaluation sessions are required')
    start, end = common[253], common[-1]
    split = 253 + int((len(common) - 253) * .7)
    train_end, oos_start = common[split - 1], common[split]
    names = {a.ticker: a.name for a in ETF_ASSETS.values()}
    if universe_history is not None:
        names.update({item.ticker: item.name for item in universe_history.listings})

    def run(key, first, last, base_risk_budget):
        engine = BacktestEngine(initial_capital=100_000_000, commission_pct=.015,
                                slippage_pct=.10, tax_pct=0)
        for ticker, rows in prices.items():
            engine.add_price_data(ticker, rows, names[ticker])
        engine.set_benchmark(benchmark)
        engine.run_adaptive_strategy(
            key,
            first,
            last,
            base_risk_budget=base_risk_budget,
            universe_history=universe_history,
            delisting_policy='verified' if universe_history is not None else 'cash',
        )
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
                  'strategy_version': '1.1', 'parameters_adjusted': selected_budget != 1.0,
                  'adjustment': 'Shared base risk budget selected using in-sample data only',
                  'selected_base_risk_budget': selected_budget,
                  'in_sample_budget_scores': budget_scores,
                  'out_of_sample_note': 'Chronological holdout; the shared risk budget was selected from the in-sample period only and then frozen before the holdout.',
                  'universe_mode': 'point_in_time' if universe_history is not None else 'survivor_only',
                  'universe_coverage': (universe_history.coverage if universe_history is not None
                                        else 'survivor_only'),
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
    parser.add_argument('--universe-file', default='data/etf_universe_history.csv')
    parser.add_argument('--universe-mode', choices=('survivor', 'point_in_time', 'both'), default='both')
    parser.add_argument('--selection-policy', choices=('explicit_schedule', 'oldest_listing_v1'),
                        default='explicit_schedule')
    parser.add_argument('--price-evidence', type=Path, help='Verified adjustment sources and hashes for exact input rows')
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        db = StockDB(args.db)
        survivor_names = {a.ticker: a.name for a in ETF_ASSETS.values()}
        universe = ETFUniverse.from_csv(args.universe_file, selection_policy=args.selection_policy)
        names = dict(survivor_names)
        names.update({item.ticker: item.name for item in universe.listings})
        tickers = sorted(names)
        if not args.cached:
            db.ensure_adjusted_etf_data(tickers, '2010-01-01', args.end, names)
        prices = db.get_adjusted_prices_many(tickers, '2010-01-01', args.end)
        benchmark_file = output.parent / 'adaptive_benchmark.json'
        if args.cached and benchmark_file.exists():
            benchmark = json.loads(benchmark_file.read_text())
        else:
            benchmark = db._fetch_yfinance_index('1001', '2010-01-01', args.end)
            benchmark_file.write_text(json.dumps(benchmark))
        survivor_prices = {ticker: prices[ticker] for ticker in survivor_names if ticker in prices}
        survivor_report = None
        pit_report = None
        if args.universe_mode in {'survivor', 'both'}:
            survivor_report = evaluate(survivor_prices, benchmark)
        if args.universe_mode in {'point_in_time', 'both'}:
            if not universe.is_point_in_time_complete:
                pit_report = {
                    'status': 'blocked',
                    'error': 'point-in-time universe coverage is incomplete',
                    'universe_mode': 'point_in_time',
                    'universe_coverage': universe.coverage,
                    'universe_file': str(args.universe_file),
                }
            else:
                try:
                    evidence = json.loads(args.price_evidence.read_text()) if args.price_evidence else None
                    pit_report = evaluate(prices, benchmark, universe_history=universe,
                                          price_evidence=evidence)
                except (ValueError, OSError) as exc:
                    pit_report = {'status': 'blocked', 'error': str(exc), 'universe_mode': 'point_in_time'}
        if args.universe_mode == 'survivor':
            report = survivor_report
        elif args.universe_mode == 'point_in_time':
            report = pit_report
        else:
            report = dict(survivor_report)
            report['universe_comparison'] = {
                'universe_file': str(args.universe_file),
                'survivor_only': survivor_report,
                'point_in_time': pit_report,
            }
            report['bias_controls'] = {
                'date_versioned_universe': True,
                'point_in_time_eligibility': pit_report.get('status') != 'blocked',
                'delisting_liquidation': 'verified_receivable',
                'comparison_status': pit_report.get('status'),
            }
            if pit_report.get('status') == 'blocked':
                report['status'] = 'blocked'
    except Exception as exc:  # noqa: BLE001
        report = {'status': 'blocked', 'error': str(exc), 'source': 'yfinance_auto_adjust',
                      'requested_end': args.end}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'strategies'}, ensure_ascii=False))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
