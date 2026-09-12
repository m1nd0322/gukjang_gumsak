#!/usr/bin/env python3
"""Archive public KIND ETF details, including delisted issues, by ISIN."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urlencode


URL = 'https://kind.krx.co.kr/disclosure/etfisudetail.do'


def parse_details(html: str) -> dict[str, str]:
    def clean(text):
        return ' '.join(unescape(re.sub(r'<[^>]+>', ' ', text)).split())

    fields = {}
    for label, content in re.findall(r'<th\b[^>]*>(.*?)</th>\s*<td\b[^>]*>(.*?)</td>', html, re.S):
        fields[clean(label)] = clean(content)
    return fields


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, default=Path('data/krx/etf_including_delisted.json'))
    parser.add_argument('--output-dir', type=Path, default=Path('data/krx'))
    parser.add_argument('--limit', type=int)
    parser.add_argument('--offline', action='store_true', help='Rebuild outputs from cached responses only')
    args = parser.parse_args()
    rows = json.loads(args.inventory.read_text())['block1']
    if args.limit is not None:
        rows = rows[:args.limit]
    cache = args.output_dir / 'kind_details'
    cache.mkdir(parents=True, exist_ok=True)

    def fetch(row):
        isin = row['full_code']
        if not re.fullmatch(r'[A-Z0-9]{12}', isin):
            raise ValueError(f'Invalid ISIN: {isin}')
        path = cache / f'{isin}.html'
        error = ''
        if not path.exists() and not args.offline:
            response = subprocess.run([
                'curl', '-fsS', '--max-time', '15', '--data',
                urlencode({'method': 'searchEftIsuDetail', 'isuCd': isin, 'menuIndex': '0'}),
                URL,
            ], capture_output=True)
            if response.returncode:
                error = f'curl_exit_{response.returncode}'
            else:
                path.write_bytes(response.stdout)
        content = path.read_bytes() if path.exists() else b''
        fields = parse_details(content.decode('utf-8', errors='replace'))
        listing = fields.get('상장일', '')
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', listing):
            listing = ''
            error = error or 'listing_date_missing'
        return {
            'ticker': row['short_code'], 'isin': isin, 'name': row['codeName'],
            'listing_date': listing, 'delisting_date': row['dellistDd'],
            'underlying_index': fields.get('기초지수명', ''),
            'tracking_multiple': fields.get('추적배수', ''),
            'source': URL + '?' + urlencode({'method': 'searchEftIsuDetail', 'isuCd': isin}),
            'source_file': str(path), 'source_sha256': hashlib.sha256(content).hexdigest(),
            'error': error,
        }

    output = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        for row in pool.map(fetch, rows):
            output.append(row)
            if len(output) % 100 == 0:
                print(f'KIND details: {len(output)}/{len(rows)}', flush=True)
    csv_path = args.output_dir / 'etf_inventory_enriched.csv'
    if output:
        with csv_path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=list(output[0]))
            writer.writeheader()
            writer.writerows(output)
    report = {
        'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'total': len(output), 'delisted': sum(bool(row['delisting_date']) for row in output),
        'listing_dates_resolved': sum(bool(row['listing_date']) for row in output),
        'unresolved': [row['ticker'] for row in output if row['error']],
        'status': 'complete' if all(not row['error'] for row in output) else 'incomplete',
        'scope': 'Listing metadata only; not historical classification, prices, or settlement evidence',
        'inventory': str(csv_path),
    }
    (args.output_dir / 'kind_sync_report.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report), flush=True)
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
