"""Point-in-time ETF universe and lifecycle data contract.

The backtester must distinguish a convenient current ETF list from the set of
products that were actually investable on a historical rebalance date. This
module keeps that distinction explicit and validates normalized KRX exports
before they are used by a backtest.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable


def _as_date(value: str | date | None) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return date.fromisoformat(text)


def _date_text(value: str | date | None) -> str:
    parsed = _as_date(value)
    return parsed.isoformat() if parsed else ""


@dataclass(frozen=True)
class ETFListing:
    key: str
    ticker: str
    name: str
    asset_class: str
    role: str
    max_weight: float
    valid_from: str
    valid_to: str | None = None
    lifecycle_event: str = "active"
    successor_ticker: str | None = None
    coverage: str = "point_in_time"
    source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "valid_from", _date_text(self.valid_from))
        object.__setattr__(self, "valid_to", _date_text(self.valid_to) or None)
        object.__setattr__(self, "ticker", str(self.ticker).strip())
        object.__setattr__(self, "key", str(self.key).strip())

    def is_active_on(self, on_date: str | date) -> bool:
        current = _as_date(on_date)
        assert current is not None
        starts = _as_date(self.valid_from)
        ends = _as_date(self.valid_to)
        assert starts is not None
        return starts <= current and (ends is None or current <= ends)


class ETFUniverse:
    """Validated listing history for logical ETF slots."""

    def __init__(self, listings: Iterable[ETFListing], source: str = "") -> None:
        self.listings = tuple(sorted(listings, key=lambda item: (item.key, item.valid_from, item.ticker)))
        self.source = source or next((item.source for item in self.listings if item.source), "")
        self.validate()
        coverage = {item.coverage for item in self.listings}
        self.coverage = next(iter(coverage)) if len(coverage) == 1 else "mixed"

    @property
    def known_tickers(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.ticker for item in self.listings))

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.key for item in self.listings))

    @property
    def is_point_in_time_complete(self) -> bool:
        return bool(self.listings) and self.coverage == "point_in_time"

    def validate(self) -> None:
        if not self.listings:
            raise ValueError("ETF universe must contain at least one listing")
        by_key: dict[str, list[ETFListing]] = defaultdict(list)
        for item in self.listings:
            if not item.key or not item.ticker or not item.name:
                raise ValueError(f"ETF listing has a missing identity field: {item!r}")
            if not item.valid_from:
                raise ValueError(f"ETF listing has no valid_from: {item.ticker}")
            if item.max_weight <= 0 or item.max_weight > 1:
                raise ValueError(f"max_weight must be in (0, 1]: {item.ticker}")
            if item.valid_to and _as_date(item.valid_to) < _as_date(item.valid_from):
                raise ValueError(f"valid_to precedes valid_from: {item.ticker}")
            if item.successor_ticker and not item.valid_to:
                raise ValueError(f"successor_ticker requires valid_to: {item.ticker}")
            by_key[item.key].append(item)
        for key, items in by_key.items():
            ordered = sorted(items, key=lambda item: _as_date(item.valid_from))
            for previous, current in zip(ordered, ordered[1:]):
                previous_end = _as_date(previous.valid_to)
                current_start = _as_date(current.valid_from)
                assert current_start is not None
                if previous_end is None or previous_end >= current_start:
                    raise ValueError(f"overlapping listings for logical key {key}: {previous.ticker}, {current.ticker}")

    def active_on(self, on_date: str | date) -> tuple[ETFListing, ...]:
        active = [item for item in self.listings if item.is_active_on(on_date)]
        by_key = {item.key: item for item in active}
        if len(by_key) != len(active):
            raise ValueError(f"multiple active listings for a logical key on {on_date}")
        return tuple(sorted(active, key=lambda item: item.key))

    def active_specs_by_ticker(self, on_date: str | date) -> dict[str, ETFListing]:
        return {item.ticker: item for item in self.active_on(on_date)}

    def risk_tickers(self, on_date: str | date) -> set[str]:
        return {item.ticker for item in self.active_on(on_date) if item.role in {"risk", "real_asset"}}

    @classmethod
    def from_csv(cls, path: str | Path) -> "ETFUniverse":
        source_path = Path(path)
        with source_path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            required = {"key", "ticker", "name", "asset_class", "role", "max_weight", "valid_from"}
            missing = required - set(reader.fieldnames or ())
            if missing:
                raise ValueError(f"ETF universe CSV is missing columns: {sorted(missing)}")
            listings = [
                ETFListing(
                    key=row["key"].strip(),
                    ticker=row["ticker"].strip(),
                    name=row["name"].strip(),
                    asset_class=row["asset_class"].strip(),
                    role=row["role"].strip(),
                    max_weight=float(row["max_weight"]),
                    valid_from=row["valid_from"].strip(),
                    valid_to=(row.get("valid_to") or "").strip() or None,
                    lifecycle_event=(row.get("lifecycle_event") or "active").strip(),
                    successor_ticker=(row.get("successor_ticker") or "").strip() or None,
                    coverage=(row.get("coverage") or "point_in_time").strip(),
                    source=(row.get("source") or "").strip(),
                )
                for row in reader
                if any((value or "").strip() for value in row.values())
            ]
        return cls(listings, source=str(source_path))

    def to_csv(self, path: str | Path) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "key", "ticker", "name", "asset_class", "role", "max_weight", "valid_from", "valid_to",
            "lifecycle_event", "successor_ticker", "coverage", "source",
        ]
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for item in self.listings:
                writer.writerow({field: getattr(item, field) or "" for field in fields})


def survivor_only_universe() -> ETFUniverse:
    """Build the legacy current-asset universe without claiming listing dates."""

    from strategy_catalog import ETF_ASSETS

    return ETFUniverse(
        ETFListing(
            key=asset.key,
            ticker=asset.ticker,
            name=asset.name,
            asset_class=asset.asset_class,
            role=asset.role,
            max_weight=asset.max_weight,
            valid_from="1900-01-01",
            coverage="survivor_only",
            lifecycle_event="survivor_only",
            source="legacy_current_asset_list; dates_are_not_listing_dates",
        )
        for asset in ETF_ASSETS.values()
    )


def load_etf_universe(path: str | Path | None) -> ETFUniverse:
    return ETFUniverse.from_csv(path) if path else survivor_only_universe()
