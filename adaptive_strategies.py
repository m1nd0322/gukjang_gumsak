"""Adaptive ETF allocation policies.

현재 모듈은 가격 이력만 입력받아 순수 목표 비중만 산출한다.
포트폴리오 상태나 거래 비용은 주입하지 않으며, 실행은 `BacktestEngine`이 담당한다.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from strategy_catalog import ETF_ASSETS


@dataclass(frozen=True)
class AllocationDecision:
    target_weights: dict[str, float]
    regime: str
    evidence: dict[str, Any]


REGIME_WEIGHTS = {
    "risk-on": {
        "069500": 0.20,
        "143850": 0.35,
        "133690": 0.20,
        "148070": 0.10,
        "132030": 0.05,
        "130680": 0.10,
    },
    "neutral": {
        "069500": 0.10,
        "143850": 0.20,
        "133690": 0.10,
        "148070": 0.25,
        "153130": 0.15,
        "132030": 0.10,
        "261240": 0.10,
    },
    "risk-off": {
        "148070": 0.25,
        "153130": 0.25,
        "132030": 0.20,
        "261240": 0.15,
    },
}


def momentum(closes: Sequence[float], lookback: int = 252) -> float | None:
    if len(closes) < lookback + 1:
        return None
    baseline = _to_float(closes[-lookback - 1])
    latest = _to_float(closes[-1])
    if baseline is None or latest is None:
        return None
    return latest / baseline - 1.0


def has_positive_trend(closes: Sequence[float], period: int = 210) -> bool:
    if len(closes) < period + 1:
        return False

    latest = _to_float(closes[-1])
    if latest is None:
        return False
    window = [_to_float(v) for v in closes[-period:]]
    if any(v is None for v in window):
        return False

    return latest > sum(window) / period


def annualized_volatility(closes: Sequence[float], lookback: int) -> float | None:
    if len(closes) < lookback + 1:
        return None
    slice_ = [_to_float(v) for v in closes[-(lookback + 1):]]
    if any(v is None for v in slice_):
        return None
    returns = [slice_[i] / slice_[i - 1] - 1 for i in range(1, len(slice_))]
    if len(returns) < 2:
        return None
    return statistics.pstdev(returns) * (252 ** 0.5)


def inverse_volatility_weights(
    closes_by_ticker: Mapping[str, Sequence[float]],
    lookback: int,
) -> dict[str, float]:
    vol_inv: dict[str, float] = {}
    for ticker, closes in closes_by_ticker.items():
        vol = annualized_volatility(closes, lookback)
        if vol is None or vol <= 0:
            continue
        vol_inv[ticker] = 1.0 / vol

    if not vol_inv:
        return {}
    return _normalize_weights(vol_inv)


def _to_float(value: float) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    if number == float("inf") or number == float("-inf"):
        return None
    return number


def _asset_specs_by_ticker() -> dict[str, Any]:
    return {spec.ticker: spec for spec in ETF_ASSETS.values()}


def _eligible_universe(closes_by_ticker: Mapping[str, Sequence[float]]) -> dict[str, Sequence[float]]:
    return {
        spec.ticker: closes_by_ticker[spec.ticker]
        for spec in _asset_specs_by_ticker().values()
        if spec.ticker in closes_by_ticker
    }


def _normalize_weights(weights: Mapping[str, float]) -> dict[str, float]:
    total = sum(value for value in weights.values() if value > 0)
    if total <= 0:
        return {}
    return {
        ticker: value / total
        for ticker, value in weights.items()
        if value > 0
    }


def _apply_cash(weights: Mapping[str, float]) -> dict[str, float]:
    clipped = {ticker: max(0.0, weight) for ticker, weight in weights.items()}
    total = sum(clipped.values())
    if total <= 1.0:
        return clipped
    return {ticker: weight / total for ticker, weight in clipped.items()}


def _normalize_with_caps(
    weights: Mapping[str, float],
    caps: Mapping[str, float],
) -> dict[str, float]:
    remaining = {
        ticker: max(0.0, weight)
        for ticker, weight in weights.items()
        if weight > 0
    }
    if not remaining:
        return {}

    fixed: dict[str, float] = {}
    while True:
        total = sum(remaining.values())
        if total <= 0:
            break

        budget = max(0.0, 1.0 - sum(fixed.values()))
        normalized = {ticker: value / total * budget for ticker, value in remaining.items()}
        violated = [
            ticker
            for ticker, weight in normalized.items()
            if weight > caps.get(ticker, 1.0)
        ]
        if not violated:
            return {**fixed, **normalized}

        for ticker in violated:
            fixed[ticker] = caps.get(ticker, 1.0)
            remaining.pop(ticker, None)

        if not remaining:
            fixed_total = sum(fixed.values())
            if fixed_total <= 0:
                return {}
            if fixed_total >= 1.0:
                return {ticker: weight / fixed_total for ticker, weight in fixed.items()}
            return fixed

    return remaining


def _apply_group_caps(
    weights: Mapping[str, float],
    groups: Sequence[tuple[set[str], float]],
) -> dict[str, float]:
    adjusted = dict(weights)
    for members, cap in groups:
        members_total = sum(adjusted.get(ticker, 0.0) for ticker in members)
        if members_total <= 0 or members_total <= cap:
            continue
        factor = cap / members_total
        for ticker in members:
            if adjusted.get(ticker, 0.0) > 0:
                adjusted[ticker] *= factor
    return adjusted


def _build_regime_ensemble(
    closes_by_ticker: Mapping[str, Sequence[float]],
) -> str:
    stock_tickers = ["069500", "143850", "133690"]
    macro_tickers = ["132030", "261240", "148070", "153130"]

    stock_up = sum(
        1
        for ticker in stock_tickers
        if momentum(closes_by_ticker.get(ticker, []), lookback=252) is not None
        and momentum(closes_by_ticker.get(ticker, []), lookback=252) > 0
        and has_positive_trend(closes_by_ticker.get(ticker, []), period=210)
    )
    macro_up = sum(
        1
        for ticker in macro_tickers
        if momentum(closes_by_ticker.get(ticker, []), lookback=252) is not None
        and momentum(closes_by_ticker.get(ticker, []), lookback=252) > 0
    )

    if stock_up >= 3:
        return "risk-on"
    if stock_up <= 1 and macro_up >= 2:
        return "risk-off"
    return "neutral"


def build_allocation(
    strategy_key: str,
    closes_by_ticker: Mapping[str, Sequence[float]],
) -> AllocationDecision:
    closes_by_ticker = dict(closes_by_ticker)
    available = _eligible_universe(closes_by_ticker)
    specs_by_ticker = _asset_specs_by_ticker()

    if strategy_key == "defensive_dual_momentum":
        risk_tickers = ["069500", "143850", "133690"]
        momentum_values: dict[str, float] = {}
        for ticker in risk_tickers:
            value = momentum(available.get(ticker, []), lookback=252)
            if value is not None:
                momentum_values[ticker] = value

        if momentum_values:
            best_ticker = max(momentum_values, key=momentum_values.get)
            best_momentum = momentum_values[best_ticker]
        else:
            best_ticker = None
            best_momentum = None

        if best_ticker is not None and best_momentum is not None and best_momentum > 0:
            raw_target = {
                best_ticker: 0.40,
                "153130": 0.50,
            }
            regime = "risk-on"
        else:
            raw_target = {"153130": 0.50}
            regime = "risk-off"

        target = _apply_cash(_apply_caps_to_assets(raw_target, specs_by_ticker))
        evidence = {"mode": "momentum"}
        return AllocationDecision(
            target_weights=target,
            regime=regime,
            evidence=evidence,
        )

    if strategy_key == "multi_asset_trend_rotation":
        candidates = []
        for ticker in available:
            if momentum(available[ticker], lookback=252) is None:
                continue
            if momentum(available[ticker], lookback=252) <= 0:
                continue
            if not has_positive_trend(available[ticker], period=210):
                continue
            candidates.append(ticker)

        candidates = sorted(
            candidates,
            key=lambda t: momentum(available[t], lookback=252) or 0,
            reverse=True,
        )[:3]
        if not candidates:
            return AllocationDecision(
                target_weights={},
                regime="neutral",
                evidence={"selected": []},
            )

        raw = inverse_volatility_weights({ticker: available[ticker] for ticker in candidates}, 60)
        weighted = _normalize_weights(raw)
        per_asset_caps = {
            ticker: min(0.40, specs_by_ticker[ticker].max_weight)
            for ticker in weighted
        }
        target = _apply_cash(_normalize_with_caps(weighted, per_asset_caps))
        return AllocationDecision(
            target_weights=target,
            regime="neutral",
            evidence={"selected": candidates},
        )

    if strategy_key == "trend_risk_parity":
        selected = [
            ticker
            for ticker in available
            if momentum(available[ticker], lookback=252) is not None
            and momentum(available[ticker], lookback=252) > 0
            and has_positive_trend(available[ticker], period=210)
        ]
        if not selected:
            return AllocationDecision(
                target_weights={},
                regime="neutral",
                evidence={"selected": []},
            )

        raw = inverse_volatility_weights({ticker: available[ticker] for ticker in selected}, 60)
        weighted = _normalize_weights(raw)

        asset_caps = {
            ticker: specs_by_ticker[ticker].max_weight
            for ticker in weighted
        }
        weighted = _normalize_with_caps(weighted, asset_caps)
        weighted = _apply_group_caps(
            weighted,
            [
                ({"069500", "143850", "133690"}, 0.60),
                ({"148070", "153130"}, 0.60),
                ({"132030", "261240"}, 0.40),
                ({"130680"}, 0.10),
            ],
        )
        final = weighted

        return AllocationDecision(
            target_weights=_apply_cash(final),
            regime="neutral",
            evidence={"selected": selected},
        )

    if strategy_key == "price_regime_ensemble":
        regime = _build_regime_ensemble(available)
        evidence = _build_regime_evidence(available)
        raw_target = dict(REGIME_WEIGHTS[regime])

        caps = {
            ticker: specs_by_ticker[ticker].max_weight
            for ticker in raw_target
        }
        target = {ticker: min(weight, caps[ticker]) for ticker, weight in raw_target.items()}
        target = _apply_cash(target)

        return AllocationDecision(
            target_weights=target,
            regime=regime,
            evidence=evidence,
        )

    raise ValueError(f"지원하지 않는 적응형 전략: {strategy_key}")


def _apply_caps_to_assets(
    weights: Mapping[str, float],
    specs_by_ticker: Mapping[str, Any],
) -> dict[str, float]:
    return {
        ticker: min(weight, specs_by_ticker[ticker].max_weight)
        for ticker, weight in weights.items()
        if ticker in specs_by_ticker and weight > 0
    }


def _build_regime_evidence(
    closes_by_ticker: Mapping[str, Sequence[float]],
) -> dict[str, float | int | str]:
    stock_tickers = ["069500", "143850", "133690"]
    stock_up = [
        1
        for ticker in stock_tickers
        if momentum(closes_by_ticker.get(ticker, []), lookback=252) is not None
        and momentum(closes_by_ticker.get(ticker, []), lookback=252) > 0
        and has_positive_trend(closes_by_ticker.get(ticker, []), period=210)
    ]
    macro_tickers = ["132030", "261240", "148070"]
    macro_up = [
        1
        for ticker in macro_tickers
        if momentum(closes_by_ticker.get(ticker, []), lookback=252) is not None
        and momentum(closes_by_ticker.get(ticker, []), lookback=252) > 0
    ]
    return {
        "stock_breadth": len(stock_up),
        "macro_up": len(macro_up),
        "regime": _build_regime_ensemble(closes_by_ticker),
    }
