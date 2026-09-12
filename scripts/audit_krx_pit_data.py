#!/usr/bin/env python3
"""Acquisition and price-integrity preflight, not a full PIT certification."""
import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path


def read_csv(path):
    with path.open(newline='', encoding='utf-8-sig') as handle:
        return list(csv.DictReader(handle))


def audit_series(rows, listing_date, delisting_date):
    issues, seen, dates = Counter(), set(), []
    for row in rows:
        try:
            day = date.fromisoformat(row['date']).isoformat()
            values = {key: float(row[key]) for key in ('open', 'high', 'low', 'close', 'volume')}
        except (KeyError, ValueError, TypeError):
            issues['malformed_rows'] += 1
            continue
        if day in seen:
            issues['duplicate_dates'] += 1
        seen.add(day)
        dates.append(day)
        if day < listing_date or (delisting_date and day > delisting_date):
            issues['outside_listing_interval'] += 1
        if not all(math.isfinite(value) for value in values.values()):
            issues['nonfinite_values'] += 1
            continue
        if min(values[key] for key in ('open', 'high', 'low', 'close')) <= 0:
            issues['nonpositive_ohlc'] += 1
        if values['volume'] < 0:
            issues['negative_volume'] += 1
        if not (values['low'] <= min(values['open'], values['close'])
                <= max(values['open'], values['close']) <= values['high']):
            issues['inconsistent_ohlc'] += 1
    if dates != sorted(dates):
        issues['unsorted_dates'] += 1
    return {'rows': len(rows), 'first_date': min(dates) if dates else None,
            'last_date': max(dates) if dates else None, 'issues': dict(issues)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, default=Path('data/krx/etf_inventory_reconciled.csv'))
    parser.add_argument('--prices', type=Path, default=Path('data/krx/vendor_prices'))
    parser.add_argument('--start', default='2010-01-01')
    parser.add_argument('--end', default=date.today().isoformat())
    parser.add_argument('--output', type=Path, default=Path('reports/krx_pit_validation.json'))
    parser.add_argument('--worklist', type=Path, default=Path('reports/krx_pit_evidence_worklist.csv'))
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start).isoformat(), date.fromisoformat(args.end).isoformat()
    if start > end:
        raise ValueError('Start must precede end')
    listings = read_csv(args.inventory)
    tickers = [row['ticker'] for row in listings]
    if len(set(tickers)) != len(tickers):
        raise ValueError('Duplicate inventory ticker; reconcile identity first')
    digests, evidence_issues, scoped, excluded, worklist, details = {}, [], [], [], [], []
    for row in listings:
        ticker, beginning, ending = row['ticker'], row['listing_date'], row['delisting_date']
        artifact = row.get('listing_date_artifact') or row.get('source_file')
        expected = row.get('listing_date_sha256') or row.get('source_sha256')
        path = Path(artifact) if artifact else None
        if not beginning or not expected or path is None or not path.is_file():
            evidence_issues.append(ticker)
        else:
            if artifact not in digests:
                digests[artifact] = hashlib.sha256(path.read_bytes()).hexdigest()
            if digests[artifact] != expected:
                evidence_issues.append(ticker)
        if beginning:
            date.fromisoformat(beginning)
        if ending:
            date.fromisoformat(ending)
        if (beginning and beginning > end) or (ending and ending < start):
            excluded.append({'ticker': ticker, 'listing_date': beginning, 'delisting_date': ending})
            continue
        scoped.append(row)
        needed = ['historical_classification_and_eligibility', 'corporate_actions_and_adjusted_price_reconciliation']
        if ending:
            needed += ['last_trading_date', 'actual_redemption_amount_and_payment_confirmation', 'redemption_unit_basis']
            price_path = args.prices / (ticker + '.csv')
            detail = audit_series(read_csv(price_path), beginning, ending) if price_path.exists() else {
                'rows': 0, 'first_date': None, 'last_date': None, 'issues': {'missing_series': 1}}
            detail['ticker'] = ticker
            detail['expected_history_start'] = max(beginning, start) if beginning else start
            detail['starts_after_required_date'] = bool(detail['first_date'] and detail['first_date'] > detail['expected_history_start'])
            detail['coverage_certified'] = False
            details.append(detail)
            if detail['issues']:
                needed.append('resolve_vendor_price_integrity_flags')
        worklist.append({'ticker': ticker, 'isin': row['isin'], 'name': row['name'],
                         'listing_date': beginning, 'delisting_date': ending,
                         'required_evidence': ';'.join(needed), 'status': 'unverified'})
    report = {
        'status': 'blocked', 'audit_kind': 'acquisition_and_input_integrity_preflight',
        'updated_at': datetime.now(timezone.utc).isoformat(),
        'scope': {'start': start, 'end': end, 'initial_positions': 'empty', 'note': 'Date overlap only; no inferred strategy membership'},
        'inventory': str(args.inventory), 'total': len(listings),
        'delisted': sum(bool(row['delisting_date']) for row in listings),
        'listing_dates_resolved': sum(bool(row['listing_date']) for row in listings),
        'missing_listing_dates': sum(not row['listing_date'] for row in listings),
        'listing_artifact_integrity_failures': evidence_issues,
        'artifact_hash_match_is_not_semantic_verification': True,
        'in_scope': len(scoped), 'excluded_by_dates': excluded,
        'in_scope_delisted': len(details),
        'delisted_vendor_series_available': sum(bool(row['rows']) for row in details),
        'missing_vendor_series': [row['ticker'] for row in details if not row['rows']],
        'vendor_series_with_integrity_flags': sum(bool(row['issues']) for row in details),
        'vendor_series_details': details,
        'adjustment_verified': False, 'settlement_records_verified': 0,
        'historical_selection_evidence_verified': False, 'worklist': str(args.worklist),
        'blockers': [
            'Independently reconcile historical universe counts and dated eligibility changes',
            'Verify corporate actions and complete executable prices, including active candidates',
            'Verify actual redemption amounts, payment dates and unit basis for relevant holdings',
            'Resolve vendor integrity flags and reconcile an official trading calendar',
            'Run survivor/PIT comparison with identical dates, costs and frozen parameters'],
        'methodology_note': 'A post-hoc rule can be tested without survivorship bias; this does not establish pre-registration or eliminate selection overfitting',
        'survivorship_bias_resolved': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.worklist.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    with args.worklist.open('w', newline='', encoding='utf-8') as handle:
        fields = ['ticker', 'isin', 'name', 'listing_date', 'delisting_date', 'required_evidence', 'status']
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(worklist)
    print(json.dumps({key: value for key, value in report.items() if key not in ('vendor_series_details', 'excluded_by_dates')}, ensure_ascii=False))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
