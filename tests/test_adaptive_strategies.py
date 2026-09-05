import unittest

from adaptive_strategies import (
    REGIME_WEIGHTS,
    AllocationDecision,
    build_allocation,
    has_positive_trend,
    inverse_volatility_weights,
    momentum,
)


def make_history(base: float, final: float, total: int = 253) -> list[float]:
    if total < 1:
        raise ValueError("total must be at least 1")
    if total == 1:
        return [final]
    return [base] * (total - 1) + [final]


def histories_with_returns(returns_by_ticker: dict[str, float]) -> dict[str, list[float]]:
    base = 100.0
    return {
        ticker: make_history(base, base * (1 + ret))
        for ticker, ret in returns_by_ticker.items()
    }


def broad_positive_histories() -> dict[str, list[float]]:
    return histories_with_returns(
        {
            "069500": 0.05,
            "143850": 0.12,
            "133690": 0.08,
            "148070": 0.10,
            "153130": 0.09,
            "132030": 0.04,
            "261240": 0.07,
            "130680": 0.11,
        }
    )


def risk_on_histories() -> dict[str, list[float]]:
    return histories_with_returns(
        {
            "069500": 0.12,
            "143850": 0.01,
            "133690": 0.04,
            "148070": 0.03,
            "153130": 0.01,
            "132030": 0.02,
            "261240": 0.03,
            "130680": 0.01,
        }
    )


def risk_off_histories() -> dict[str, list[float]]:
    return histories_with_returns(
        {
            "069500": -0.05,
            "143850": 0.01,
            "133690": -0.03,
            "148070": 0.03,
            "153130": -0.01,
            "132030": 0.03,
            "261240": 0.03,
            "130680": -0.01,
        }
    )


class AdaptiveIndicatorTest(unittest.TestCase):
    def test_momentum_uses_exactly_252_prior_sessions(self):
        closes = [100.0] * 252 + [110.0]
        self.assertAlmostEqual(momentum(closes, 252), 0.10)
        self.assertIsNone(momentum(closes[:252], 252))

    def test_inverse_volatility_weights_are_normalized(self):
        weights = inverse_volatility_weights(
            {
                "LOW": [100, 101, 102, 103, 104],
                "HIGH": [100, 110, 90, 115, 85],
            },
            lookback=4,
        )
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertGreater(weights["LOW"], weights["HIGH"])

    def test_long_trend_requires_price_above_210_day_average(self):
        self.assertTrue(has_positive_trend([100.0] * 210 + [101.0], 210))
        self.assertFalse(has_positive_trend([100.0] * 210 + [99.0], 210))


class AdaptiveAllocationTest(unittest.TestCase):
    def test_dual_momentum_selects_best_positive_risk_asset(self):
        histories = histories_with_returns(
            {"069500": 0.05, "143850": 0.12, "133690": 0.08},
        )
        decision = build_allocation("defensive_dual_momentum", histories)

        self.assertIsInstance(decision, AllocationDecision)
        self.assertEqual(decision.target_weights, {"143850": 0.40, "153130": 0.50})
        self.assertEqual(decision.regime, "risk-on")

    def test_dual_momentum_falls_back_to_short_bond_when_all_negative(self):
        histories = histories_with_returns(
            {"069500": -0.05, "143850": -0.03, "133690": -0.08},
        )
        decision = build_allocation("defensive_dual_momentum", histories)
        self.assertEqual(decision.target_weights, {"153130": 0.50})
        self.assertEqual(decision.regime, "risk-off")

    def test_trend_rotation_selects_three_assets_and_caps_oil(self):
        histories = broad_positive_histories()
        decision = build_allocation("multi_asset_trend_rotation", histories)

        self.assertLessEqual(
            decision.target_weights.get("130680", 0.0),
            0.10,
        )
        self.assertLessEqual(
            len([weight for weight in decision.target_weights.values() if weight > 0]),
            3,
        )
        self.assertLessEqual(sum(decision.target_weights.values()), 1.0)

    def test_regime_ensemble_has_fixed_risk_off_allocation(self):
        histories = risk_off_histories()
        decision = build_allocation("price_regime_ensemble", histories)

        self.assertEqual(decision.regime, "risk-off")
        self.assertEqual(
            decision.target_weights,
            {
                "148070": 0.25,
                "153130": 0.25,
                "132030": 0.20,
                "261240": 0.15,
            },
        )

    def test_regime_ensemble_uses_regime_weight_table(self):
        decision = build_allocation("price_regime_ensemble", risk_on_histories())
        self.assertEqual(decision.regime, "risk-on")
        self.assertEqual(decision.target_weights, REGIME_WEIGHTS["risk-on"])


if __name__ == "__main__":
    unittest.main()
