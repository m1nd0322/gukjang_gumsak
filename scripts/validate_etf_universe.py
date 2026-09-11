#!/usr/bin/env python3
"""Validate a normalized point-in-time ETF universe export."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_universe import ETFUniverse


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    universe = ETFUniverse.from_csv(args.path)
    print(
        f"valid: listings={len(universe.listings)} keys={len(universe.keys)} "
        f"tickers={len(universe.known_tickers)} coverage={universe.coverage}"
    )
    if not universe.is_point_in_time_complete:
        print("warning: coverage is not point_in_time; PIT backtests must remain blocked")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
