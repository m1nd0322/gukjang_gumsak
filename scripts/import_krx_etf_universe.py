#!/usr/bin/env python3
"""Normalize a KRX ETF listing export into the backtest universe contract."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_universe import ETFListing, ETFUniverse


ALIASES = {
    "key": ("key", "logical_key", "전략키"),
    "ticker": ("ticker", "code", "isu_cd", "단축코드", "종목코드"),
    "name": ("name", "short_name", "isu_nm", "한글종목약명", "종목명"),
    "asset_class": ("asset_class", "asset", "자산군"),
    "role": ("role", "역할"),
    "max_weight": ("max_weight", "weight_cap", "최대편입비중"),
    "valid_from": ("valid_from", "listing_date", "상장일"),
    "valid_to": ("valid_to", "delisting_date", "상장폐지일"),
    "lifecycle_event": ("lifecycle_event", "event", "상태"),
    "successor_ticker": ("successor_ticker", "successor", "승계종목코드"),
    "coverage": ("coverage",),
}


def _value(row: dict[str, str], field: str, default: str = "") -> str:
    for name in ALIASES[field]:
        if row.get(name) not in (None, ""):
            return str(row[name]).strip()
    return default


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="KRX CSV export")
    parser.add_argument("--output", required=True, type=Path, help="normalized universe CSV")
    parser.add_argument("--source", default="KRX ETF finder export")
    parser.add_argument('--inventory-only', action='store_true', help='Import metadata, without claiming strategy eligibility')
    parser.add_argument('--mapping', type=Path, help='Dated strategy slot assignments keyed by ticker')
    parser.add_argument('--settlements', type=Path, help='Documented redemption events keyed by ticker')
    parser.add_argument('--quarantine-output', type=Path)
    parser.add_argument('--selection-policy', choices=('explicit_schedule', 'oldest_listing_v1'),
                        default='explicit_schedule')
    args = parser.parse_args()

    with args.input.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit("input CSV contains no rows")

    def load_overlay(path):
        if path is None:
            return {}
        with path.open(newline='', encoding='utf-8-sig') as handle:
            records = list(csv.DictReader(handle))
        result = {row['ticker']: row for row in records}
        if len(result) != len(records):
            raise ValueError('Duplicate ticker in overlay; import dated intervals separately')
        return result

    mappings, settlements = load_overlay(args.mapping), load_overlay(args.settlements)
    listings: list[ETFListing] = []
    rejected = []
    for index, row in enumerate(rows, start=2):
        ticker = _value(row, "ticker")
        row = {**row, **mappings.get(ticker, {}), **settlements.get(ticker, {})}
        name = _value(row, "name")
        valid_from = _value(row, "valid_from")
        if not ticker or not name or not valid_from:
            if args.quarantine_output:
                rejected.append({'row': index, 'ticker': ticker, 'error': 'missing_identity_or_listing_date'})
                continue
            raise SystemExit(f"row {index}: ticker, name, and valid_from are required")
        if not args.inventory_only and any(not _value(row, field)
                                           for field in ('key', 'asset_class', 'role', 'max_weight')):
            raise SystemExit(f'row {index}: explicit strategy mapping is required')
        valid_to = _value(row, "valid_to") or None
        event = _value(row, "lifecycle_event") or ("delisted" if valid_to else "active")
        listings.append(
            ETFListing(
                key=f'inventory_{ticker}' if args.inventory_only else _value(row, 'key'),
                ticker=ticker,
                name=name,
                asset_class=_value(row, 'asset_class', 'unclassified'),
                role=_value(row, 'role', 'unclassified'),
                max_weight=float(_value(row, 'max_weight', '1.0')),
                valid_from=valid_from,
                valid_to=valid_to,
                lifecycle_event=event,
                successor_ticker=_value(row, "successor_ticker") or None,
                coverage='inventory_only' if args.inventory_only else _value(row, 'coverage', 'unverified'),
                source=row.get('source') or args.source,
                known_from=row.get('known_from') or None,
                settlement_amount=float(row['settlement_amount']) if row.get('settlement_amount') else None,
                settlement_date=row.get('settlement_date') or None,
                settlement_known_at=row.get('settlement_known_at') or None,
                settlement_adjustment_factor=(float(row['settlement_adjustment_factor'])
                                              if row.get('settlement_adjustment_factor') else None),
                settlement_source=row.get('settlement_source') or '',
                last_trading_date=row.get('last_trading_date') or None,
                trading_end_source=row.get('trading_end_source') or '',
            )
        )
    if args.quarantine_output:
        import json
        args.quarantine_output.parent.mkdir(parents=True, exist_ok=True)
        args.quarantine_output.write_text(json.dumps(rejected, indent=2) + '\n')
    if rejected and not args.inventory_only:
        raise SystemExit('Strategy import rejected: incomplete universe')
    universe = ETFUniverse(listings, source=args.source, selection_policy=args.selection_policy)
    universe.to_csv(args.output)
    print(f"normalized {len(universe.listings)} ETF listings; quarantined={len(rejected)} -> {args.output}")
    return 1 if rejected else 0


if __name__ == "__main__":
    raise SystemExit(main())
