"""Previous-close drawdown control and staged recovery."""
from dataclasses import dataclass, field

from strategy_catalog import ETF_ASSETS


@dataclass
class DrawdownGuard:
    peak: float
    base_risk_budget: float = 0.5
    state: str = 'normal'
    budget: float = 1.0
    wait: int = 0
    stage_days: int = 0
    recovery_low: float = 0.0
    events: list = field(default_factory=list)

    def apply(self, date, equity, weights, regime):
        previous, old_budget = self.state, self.budget
        self.peak = max(self.peak, equity)
        drawdown = max(0.0, 1 - equity / self.peak)
        if self.state == 'cash':
            self.wait += 1
            if self.wait >= 20 and regime != 'risk-off':
                self.state, self.budget = 'reentry', 0.25
                self.recovery_low, self.stage_days = equity, 0
        elif self.state == 'reentry':
            if regime == 'risk-off' or equity < self.recovery_low:
                self.state, self.budget, self.wait = 'cash', 0.0, 0
            else:
                self.stage_days += 1
                if self.stage_days >= 5:
                    self.budget = min(1.0, self.budget + 0.25)
                    self.stage_days = 0
                    if self.budget == 1.0:
                        self.state, self.peak = 'normal', equity
        elif drawdown >= 0.10 - 1e-12:
            self.state, self.budget, self.wait = 'cash', 0.0, 0
        elif drawdown >= 0.08 - 1e-12:
            self.state, self.budget = 'throttled', 0.5
        else:
            self.state, self.budget = 'normal', 1.0
        if previous != self.state or old_budget != self.budget:
            self.events.append({'date': date, 'previous_state': previous,
                                    'state': self.state, 'drawdown': drawdown,
                                    'action': self.state, 'risk_budget': self.budget})
        risk = {a.ticker for a in ETF_ASSETS.values() if a.role in {'risk', 'real_asset'}}
        if self.state == 'cash':
            return {}
        return {t: w * (self.base_risk_budget if t in risk else 1)
                * (self.budget if self.state == 'reentry' or t in risk else 1)
                for t, w in weights.items()}
