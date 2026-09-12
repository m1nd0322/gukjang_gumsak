#!/usr/bin/env python3
"""Resume year-bounded KRX daily-price acquisition; never certify adjustment."""
import argparse
import csv
import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

import requests

from sync_krx_authenticated_master import BASE, DATA, LOGIN, credentials


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, default=Path('data/krx/etf_inventory_reconciled.csv'))
    parser.add_argument('--output-dir', type=Path, default=Path('data/krx/official_prices'))
    parser.add_argument('--account-file', type=Path, default=Path('/Users/songhear/APIs/krx_login_account.txt'))
    parser.add_argument('--start', default='2010-01-01')
    parser.add_argument('--end', default='2026-09-11')
    parser.add_argument('--workers', type=int, choices=(1, 2), default=1)
    parser.add_argument('--limit', type=int, default=0, help='Maximum tickers; zero means all')
    args = parser.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if start > end or args.limit < 0:
        raise ValueError('Invalid date range or limit')
    with args.inventory.open(newline='', encoding='utf-8-sig') as handle:
        inventory = list(csv.DictReader(handle))
    listings = [row for row in inventory if row['delisting_date']
                and row['delisting_date'] >= start.isoformat()
                and row['listing_date'] <= end.isoformat()]
    listings.sort(key=lambda row: row['ticker'])
    if args.limit:
        listings = listings[:args.limit]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    headers = {'User-Agent': 'Mozilla/5.0', 'X-Requested-With': 'XMLHttpRequest',
               'Referer': BASE + '/contents/MDC/COMS/client/view/login.jsp?site=mdc'}
    with requests.Session() as login:
        login.headers.update(headers)
        login.get(headers['Referer'], timeout=15).raise_for_status()
        response = login.post(LOGIN, data={**credentials(args.account_file), 'skipDup': 'Y'},
                              timeout=15, allow_redirects=False)
        response.raise_for_status()
        result = response.json()
        if result.get('_error_code') != 'CD001' or not result.get('MBR_NO'):
            raise ValueError('Authentication unsuccessful; no retries')
        cookies = login.cookies.copy()
    headers['Referer'] = BASE + '/contents/MDC/MDI/mdiLoader/index.cmd?menuId=MDC0201030101'
    stop = threading.Event()

    def fetch(row):
        ticker = row['ticker']
        first = max(start, date.fromisoformat(row['listing_date']))
        last = min(end, date.fromisoformat(row['delisting_date']))
        parts = []
        with requests.Session() as session:
            session.headers.update(headers)
            session.cookies.update(cookies)
            for year in range(first.year, last.year + 1):
                if stop.is_set():
                    break
                low, high = max(first, date(year, 1, 1)), min(last, date(year, 12, 31))
                # Date-bounded filenames prevent reuse for a different requested interval.
                artifact = args.output_dir / f'{ticker}_{low:%Y%m%d}_{high:%Y%m%d}.json'
                params = {'bld': 'dbms/MDC/STAT/standard/MDCSTAT04501', 'locale': 'ko_KR',
                          'isuCd': row['isin'], 'strtDd': low.strftime('%Y%m%d'),
                          'endDd': high.strftime('%Y%m%d'), 'share': '1', 'money': '1',
                          'csvxls_isNo': 'false'}
                try:
                    if artifact.exists():
                        payload = json.loads(artifact.read_text())
                    else:
                        time.sleep(.35)
                        response = session.post(DATA, data=params, timeout=15, allow_redirects=False)
                        if response.status_code != 200:
                            stop.set()
                            parts.append({'year': year, 'status': 'http_error',
                                          'http_status': response.status_code})
                            break
                        payload = response.json()
                    rows = payload.get('output')
                    if not isinstance(rows, list):
                        raise ValueError('Missing output array')
                    dates = [date.fromisoformat(item['TRD_DD'].replace('/', '-')).isoformat()
                             for item in rows]
                    if any(day < low.isoformat() or day > high.isoformat() for day in dates):
                        raise ValueError('Returned dates outside requested interval')
                    if len(set(dates)) != len(dates):
                        raise ValueError('Duplicate official date')
                    if not artifact.exists():
                        temporary = artifact.with_suffix('.tmp')
                        temporary.write_text(json.dumps(payload, ensure_ascii=False))
                        temporary.replace(artifact)
                    parts.append({'year': year, 'status': 'downloaded' if rows else 'empty',
                                  'rows': len(rows), 'first_date': min(dates) if dates else None,
                                  'last_date': max(dates) if dates else None,
                                  'artifact': str(artifact), 'params': params,
                                  'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest()})
                except (requests.RequestException, ValueError, KeyError) as exc:
                    stop.set()
                    parts.append({'year': year, 'status': 'error', 'error_type': type(exc).__name__})
                    break
        complete = len(parts) == last.year - first.year + 1 and all(
            part['status'] == 'downloaded' for part in parts)
        record = {'ticker': ticker, 'isin': row['isin'], 'parts': parts,
                  'requested_years': last.year - first.year + 1,
                  'all_years_nonempty': complete, 'adjustment_verified': False,
                  'trading_session_coverage_verified': False}
        (args.output_dir / f'{ticker}_year_manifest.json').write_text(json.dumps(record, indent=2) + '\n')
        return record

    records = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(fetch, row) for row in listings]
        for future in as_completed(futures):
            records.append(future.result())
            if len(records) % 10 == 0:
                print(json.dumps({'processed': len(records), 'total': len(listings),
                                  'all_years_nonempty': sum(row['all_years_nonempty'] for row in records)}), flush=True)
    report = {'start': start.isoformat(), 'end': end.isoformat(), 'source': DATA,
              'total_requested': len(listings), 'stopped_on_error': stop.is_set(),
              'all_years_nonempty': sum(row['all_years_nonempty'] for row in records),
              'adjustment_verified': False, 'records': sorted(records, key=lambda row: row['ticker'])}
    (args.output_dir / 'year_manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'records'}), flush=True)
    return 0 if report['all_years_nonempty'] == len(listings) and listings else 1


if __name__ == '__main__':
    raise SystemExit(main())
