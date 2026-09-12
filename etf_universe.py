"""Point-in-time ETF universe and lifecycle data contract.

The backtester must distinguish a convenient current ETF list from the set of
products that were actually investable on a historical rebalance date. This
module keeps that distinction explicit and validates normalized KRX exports
before they are used by a backtest.
"""

from __future__ import annotations

import csv
import math
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
    known_from: str | None = None
    settlement_amount: float | None = None
    settlement_date: str | None = None
    settlement_known_at: str | None = None
    settlement_adjustment_factor: float | None = None
    settlement_source: str = ""
    last_trading_date: str | None = None
    trading_end_source: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "valid_from", _date_text(self.valid_from))
        object.__setattr__(self, "valid_to", _date_text(self.valid_to) or None)
        object.__setattr__(self, "ticker", str(self.ticker).strip())
        object.__setattr__(self, "key", str(self.key).strip())
        for field in ('known_from', 'settlement_date', 'settlement_known_at', 'last_trading_date'):
            object.__setattr__(self, field, _date_text(getattr(self, field)) or None)

    def is_active_on(self, on_date: str | date) -> bool:
        current = _as_date(on_date)
        assert current is not None
        starts = _as_date(self.valid_from)
        ends = _as_date(self.last_trading_date or self.valid_to)
        assert starts is not None
        return starts <= current and (ends is None or current <= ends)


class ETFUniverse:
    """Validated listing history for logical ETF slots."""

    def __init__(self, listings: Iterable[ETFListing], source: str = "",
                 selection_policy: str = 'explicit_schedule') -> None:
        if selection_policy not in {'explicit_schedule', 'oldest_listing_v1'}:
            raise ValueError('Unsupported selection policy')
        self.selection_policy = selection_policy
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
        from strategy_catalog import ETF_ASSETS

        return self.coverage == "point_in_time" and all(
            item.source and item.known_from and item.known_from <= item.valid_from
            and item.key in ETF_ASSETS
            and (not item.valid_to or (item.last_trading_date and item.trading_end_source))
            for item in self.listings
        )

    def validate(self) -> None:
        if not self.listings:
            raise ValueError("ETF universe must contain at least one listing")
        by_key: dict[str, list[ETFListing]] = defaultdict(list)
        for item in self.listings:
            if not item.key or not item.ticker or not item.name:
                raise ValueError(f"ETF listing has a missing identity field: {item!r}")
            if not item.valid_from:
                raise ValueError(f"ETF listing has no valid_from: {item.ticker}")
            if not math.isfinite(item.max_weight) or item.max_weight <= 0 or item.max_weight > 1:
                raise ValueError(f"max_weight must be in (0, 1]: {item.ticker}")
            if item.valid_to and _as_date(item.valid_to) < _as_date(item.valid_from):
                raise ValueError(f"valid_to precedes valid_from: {item.ticker}")
            if item.successor_ticker and not item.valid_to:
                raise ValueError(f"successor_ticker requires valid_to: {item.ticker}")
            if item.last_trading_date and (
                    not item.valid_to or not item.trading_end_source
                    or not item.valid_from <= item.last_trading_date <= item.valid_to):
                raise ValueError(f'Invalid last-trading-date evidence: {item.ticker}')
            if item.settlement_amount is not None:
                if (not math.isfinite(item.settlement_amount) or item.settlement_amount < 0
                        or not item.valid_to or not item.settlement_date
                        or not item.settlement_known_at or not item.settlement_source
                        or item.settlement_adjustment_factor is None
                        or not math.isfinite(item.settlement_adjustment_factor)
                        or item.settlement_adjustment_factor <= 0
                        or item.settlement_date <= item.valid_to
                        or item.settlement_known_at > item.settlement_date):
                    raise ValueError(f'Incomplete or invalid settlement evidence: {item.ticker}')
            by_key[item.key].append(item)
        for key, items in by_key.items():
            ordered = sorted(items, key=lambda item: _as_date(item.valid_from))
            for previous, current in zip(ordered, ordered[1:]):
                previous_end = _as_date(previous.valid_to)
                current_start = _as_date(current.valid_from)
                assert current_start is not None
                if (self.selection_policy == 'explicit_schedule'
                        and (previous_end is None or previous_end >= current_start)):
                    raise ValueError(f"overlapping listings for logical key {key}: {previous.ticker}, {current.ticker}")

    def active_on(self, on_date: str | date, histories=None) -> tuple[ETFListing, ...]:
        current = _date_text(on_date)
        active = [item for item in self.listings if item.is_active_on(on_date)
                  and (not item.known_from or item.known_from < current)]
        if histories is not None:
            active = [item for item in active if len(histories.get(item.ticker, [])) >= 253]
        by_key = {}
        for item in sorted(active, key=lambda item: (item.valid_from, item.ticker)):
            by_key.setdefault(item.key, item)
        if self.selection_policy == 'explicit_schedule' and len(by_key) != len(active):
            raise ValueError(f"multiple active listings for a logical key on {on_date}")
        return tuple(sorted(by_key.values(), key=lambda item: item.key))

    def active_specs_by_ticker(self, on_date: str | date, histories=None) -> dict[str, ETFListing]:
        return {item.ticker: item for item in self.active_on(on_date, histories)}

    def risk_tickers(self, on_date: str | date) -> set[str]:
        return {item.ticker for item in self.active_on(on_date) if item.role in {"risk", "real_asset"}}

    @classmethod
    def from_csv(cls, path: str | Path, selection_policy='explicit_schedule') -> "ETFUniverse":
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
                    known_from=(row.get('known_from') or '').strip() or None,
                    settlement_amount=(float(row['settlement_amount'])
                                       if row.get('settlement_amount') else None),
                    settlement_date=row.get('settlement_date') or None,
                    settlement_known_at=row.get('settlement_known_at') or None,
                    settlement_adjustment_factor=(float(row['settlement_adjustment_factor'])
                                                  if row.get('settlement_adjustment_factor') else None),
                    settlement_source=row.get('settlement_source') or '',
                    last_trading_date=row.get('last_trading_date') or None,
                    trading_end_source=row.get('trading_end_source') or '',
                )
                for row in reader
                if any((value or "").strip() for value in row.values())
            ]
        return cls(listings, source=str(source_path), selection_policy=selection_policy)

    def to_csv(self, path: str | Path) -> None:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "key", "ticker", "name", "asset_class", "role", "max_weight", "valid_from", "valid_to",
            "lifecycle_event", "successor_ticker", "coverage", "source",
            'known_from', 'settlement_amount', 'settlement_date', 'settlement_known_at',
            'settlement_adjustment_factor', 'settlement_source',
            'last_trading_date', 'trading_end_source',
        ]
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for item in self.listings:
                writer.writerow({field: getattr(item, field) if getattr(item, field) is not None else ""
                                 for field in fields})


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
