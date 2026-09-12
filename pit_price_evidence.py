"""Bind adjustment evidence to the exact price rows used by a PIT run."""
import hashlib
import json


def price_digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def require_adjusted_price_evidence(universe, prices, evidence):
    if not evidence:
        raise ValueError('Verified corporate-action price evidence is required for PIT evaluation')
    records = evidence.get('tickers', {})
    for ticker in universe.known_tickers:
        row = records.get(ticker, {})
        if (not prices.get(ticker) or row.get('adjustment_verified') is not True
                or not row.get('source') or not row.get('corporate_actions_source')
                or row.get('prices_sha256') != price_digest(prices[ticker])):
            raise ValueError(f'Missing, unverified, or stale adjusted-price evidence: {ticker}')
