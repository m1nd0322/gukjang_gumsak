#!/usr/bin/env python3
"""Compare vendor history to archived KRX prices without inferring adjustments."""
import argparse
import csv
import hashlib
import json
import math
from collections import Counter
from pathlib import Path


FIELDS = {'open': 'TDD_OPNPRC', 'high': 'TDD_HGPRC', 'low': 'TDD_LWPRC',
          'close': 'TDD_CLSPRC', 'volume': 'ACC_TRDVOL', 'nav': 'LST_NAV'}


def number(value):
    try:
        parsed = float(str(value).replace(',', ''))
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, default=Path('data/krx/official_prices/year_manifest.json'))
    parser.add_argument('--vendor-dir', type=Path, default=Path('data/krx/vendor_prices'))
    parser.add_argument('--output', type=Path, default=Path('reports/krx_price_reconciliation.json'))
    parser.add_argument('--normalized-dir', type=Path, default=Path('data/krx/official_prices/normalized_raw'))
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    args.normalized_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for record in manifest['records']:
        ticker = record['ticker']
        official = {}
        for part in record['parts']:
            if part['status'] not in ('downloaded', 'empty'):
                continue
            raw = Path(part['artifact']).read_bytes()
            if hashlib.sha256(raw).hexdigest() != part['sha256']:
                raise ValueError(f'Official artifact hash mismatch: {ticker}')
            for row in json.loads(raw)['output']:
                day = row['TRD_DD'].replace('/', '-')
                if day in official:
                    raise ValueError(f'Duplicate official date: {ticker} {day}')
                official[day] = {'date': day, **{key: number(row.get(source))
                                                for key, source in FIELDS.items()}}
        vendor_path = args.vendor_dir / f'{ticker}.csv'
        vendor = {}
        duplicates = 0
        if vendor_path.exists():
            with vendor_path.open(newline='') as handle:
                for row in csv.DictReader(handle):
                    if row['date'] in vendor:
                        duplicates += 1
                    if manifest['start'] <= row['date'] <= manifest['end']:
                        vendor[row['date']] = row
        common = sorted(official.keys() & vendor.keys())
        differences, ratios, examples = Counter(), [], []
        for day in common:
            for field in ('open', 'high', 'low', 'close', 'volume'):
                actual, quoted = official[day][field], number(vendor[day].get(field))
                if actual is None or quoted is None:
                    differences['non_numeric_' + field] += 1
                elif actual != quoted:
                    differences[field] += 1
                    if len(examples) < 5:
                        examples.append({'date': day, 'field': field, 'krx': actual, 'vendor': quoted})
            actual, quoted = official[day]['close'], number(vendor[day].get('close'))
            if actual and actual > 0 and quoted and quoted > 0:
                ratios.append(quoted / actual)
        executable = [row for row in official.values()
                      if all(row[field] is not None and row[field] > 0
                             for field in ('open', 'high', 'low', 'close', 'volume'))
                      and row['low'] <= min(row['open'], row['close'])
                      <= max(row['open'], row['close']) <= row['high']]
        normalized = args.normalized_dir / f'{ticker}.csv'
        with normalized.open('w', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=['date', *FIELDS])
            writer.writeheader()
            writer.writerows(official[day] for day in sorted(official))
        records.append({
            'ticker': ticker, 'all_requested_years_nonempty': record['all_years_nonempty'],
            'official_rows': len(official), 'vendor_rows_in_scope': len(vendor),
            'overlapping_dates': len(common), 'vendor_duplicate_dates': duplicates,
            'official_dates_missing_from_vendor': sorted(official.keys() - vendor.keys()),
            'vendor_dates_not_returned_by_krx': sorted(vendor.keys() - official.keys()),
            'field_difference_counts': dict(differences), 'difference_examples': examples,
            'vendor_to_official_close_ratio_range': [min(ratios), max(ratios)] if ratios else None,
            'positive_volume_valid_ohlc_rows': len(executable),
            'last_observed_positive_volume_date': max((row['date'] for row in executable), default=None),
            'last_observation_is_not_official_trading_end_evidence': True,
            'normalized_raw_file': str(normalized),
            'normalized_sha256': hashlib.sha256(normalized.read_bytes()).hexdigest(),
            'adjustment_verified': False,
        })
    report = {
        'status': 'unverified_adjustments', 'source_manifest': str(args.manifest),
        'tickers': len(records), 'official_rows': sum(row['official_rows'] for row in records),
        'all_requested_years_nonempty': sum(row['all_requested_years_nonempty'] for row in records),
        'tickers_with_price_differences': sum(any(row['field_difference_counts'].get(key, 0)
                                                for key in ('open', 'high', 'low', 'close')) for row in records),
        'adjustment_verified': False, 'survivorship_bias_resolved': False,
        'warning': 'Raw KRX and vendor adjusted prices may legitimately differ. Ratios alone do not prove corporate-action coverage. Never use NAV as actual redemption payment.',
        'records': records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'records'}))


if __name__ == '__main__':
    main()
