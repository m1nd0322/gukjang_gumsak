#!/usr/bin/env python3
"""Archive available vendor OHLCV; adjustment coverage remains unverified."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inventory', type=Path, default=Path('data/krx/etf_including_delisted.json'))
    parser.add_argument('--output-dir', type=Path, default=Path('data/krx/vendor_prices'))
    args = parser.parse_args()
    rows = [row for row in json.loads(args.inventory.read_text())['block1'] if row['dellistDd']]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    def fetch(row):
        ticker = row['short_code']
        if not re.fullmatch(r'[A-Z0-9]{6}', ticker):
            raise ValueError('Invalid ticker')
        url = 'https://fchart.stock.naver.com/sise.nhn?' + urlencode({
            'symbol': ticker, 'timeframe': 'day', 'count': '10000', 'requestType': '0',
        })
        path = args.output_dir / f'{ticker}.xml'
        error = ''
        if not path.exists():
            result = subprocess.run(['curl', '-fsS', '--max-time', '15', url], capture_output=True)
            if result.returncode:
                error = f'curl_exit_{result.returncode}'
            else:
                path.write_bytes(result.stdout)
        content = path.read_bytes() if path.exists() else b''
        items = re.findall(rb'<item\s+data="([^"]+)"', content)
        prices = []
        for item in items:
            parts = item.decode('ascii').split('|')
            if len(parts) != 6 or not re.fullmatch(r'\d{8}', parts[0]):
                error = 'invalid_price_row'
                continue
            d, opening, high, low, close, volume = parts
            prices.append([f'{d[:4]}-{d[4:6]}-{d[6:]}', opening, high, low, close, volume])
        if prices:
            with (args.output_dir / f'{ticker}.csv').open('w', newline='') as handle:
                writer = csv.writer(handle)
                writer.writerow(['date', 'open', 'high', 'low', 'close', 'volume'])
                writer.writerows(prices)
        return {
            'ticker': ticker, 'delisting_date': row['dellistDd'], 'rows': len(prices),
            'first_date': prices[0][0] if prices else None,
            'last_date': prices[-1][0] if prices else None,
            'source': url, 'sha256': hashlib.sha256(content).hexdigest(),
            'adjustment_verified': False, 'error': error or ('' if prices else 'empty_series'),
        }

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(fetch, rows))
    report = {
        'retrieved_at': datetime.now(timezone.utc).isoformat(),
        'total_delisted': len(rows),
        'with_prices': sum(bool(row['rows']) for row in results),
        'adjustment_verified': False,
        'scope': 'Vendor OHLCV only; distributions/splits and settlement are not verified',
        'issues': results,
    }
    (args.output_dir / 'manifest.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'issues'}))


if __name__ == '__main__':
    main()
