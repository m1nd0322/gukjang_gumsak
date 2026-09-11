#!/usr/bin/env python3
"""Normalize a KRX ETF listing export into the backtest universe contract."""

from __future__ import annotations

import argparse
import csv
import re
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


def _slug(value: str) -> str:
    ascii_value = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return ascii_value or "unknown_asset"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="KRX CSV export")
    parser.add_argument("--output", required=True, type=Path, help="normalized universe CSV")
    parser.add_argument("--source", default="KRX ETF finder export")
    parser.add_argument("--default-asset-class", default="equity")
    parser.add_argument("--default-role", default="risk")
    parser.add_argument("--default-max-weight", type=float, default=1.0)
    args = parser.parse_args()

    with args.input.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise SystemExit("input CSV contains no rows")

    listings: list[ETFListing] = []
    for index, row in enumerate(rows, start=2):
        ticker = _value(row, "ticker")
        name = _value(row, "name")
        valid_from = _value(row, "valid_from")
        if not ticker or not name or not valid_from:
            raise SystemExit(f"row {index}: ticker, name, and valid_from are required")
        valid_to = _value(row, "valid_to") or None
        event = _value(row, "lifecycle_event") or ("delisted" if valid_to else "active")
        listings.append(
            ETFListing(
                key=_value(row, "key") or (
                    _slug(name) if _slug(name) != "unknown_asset" else f"ticker_{ticker}"
                ),
                ticker=ticker,
                name=name,
                asset_class=_value(row, "asset_class", args.default_asset_class),
                role=_value(row, "role", args.default_role),
                max_weight=float(_value(row, "max_weight", str(args.default_max_weight))),
                valid_from=valid_from,
                valid_to=valid_to,
                lifecycle_event=event,
                successor_ticker=_value(row, "successor_ticker") or None,
                coverage=_value(row, "coverage", "point_in_time"),
                source=args.source,
            )
        )
    universe = ETFUniverse(listings, source=args.source)
    universe.to_csv(args.output)
    print(f"normalized {len(universe.listings)} ETF listings -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
