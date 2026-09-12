from dataclasses import replace

import pytest

from adaptive_strategies import build_allocation_for_universe
from backtester import BacktestEngine, CostConfig, Portfolio
from datetime import date, timedelta
from etf_universe import ETFListing, ETFUniverse
from pit_price_evidence import price_digest, require_adjusted_price_evidence
from scripts.sync_krx_etf_details import parse_details


def listing(**kwargs):
    values = dict(key='kr_equity', ticker='OLD', name='Fixture', asset_class='equity',
                  role='risk', max_weight=.4, valid_from='2020-01-01',
                  source='fixture', known_from='2019-12-31')
    return ETFListing(**{**values, **kwargs})


def test_kind_listing_parser():
    assert parse_details('<th scope="row">상장일</th><td>\n2015-08-26\n</td>')['상장일'] == '2015-08-26'


def test_oldest_rule_uses_only_previously_known_eligible_candidates():
    old = listing()
    new = listing(ticker='NEW', valid_from='2020-02-01')
    universe = ETFUniverse([old, new], selection_policy='oldest_listing_v1')
    assert universe.active_on('2021-01-01', {'OLD': [1] * 252, 'NEW': [1] * 253}) == (new,)
    assert universe.active_on('2021-01-01', {'OLD': [1] * 253, 'NEW': [1] * 253}) == (old,)
    future = ETFUniverse([replace(old, known_from='2021-02-01')])
    assert not future.active_on('2021-01-01')
    assert not future.is_point_in_time_complete


def test_current_ticker_cannot_leak_into_historical_allocation():
    histories = {'OLD': list(range(100, 360)), '153130': [100] * 260}
    decision = build_allocation_for_universe('defensive_dual_momentum', histories, [listing()])
    assert decision.target_weights == {'OLD': .4}


def test_redemption_waits_until_payment_and_has_no_market_exit_fees():
    event = listing(valid_to='2020-06-30', settlement_amount=80,
                    settlement_date='2020-07-10', settlement_known_at='2020-07-09',
                    settlement_adjustment_factor=.5, settlement_source='fixture settlement')
    ETFUniverse([event])
    portfolio = Portfolio(1000, CostConfig(slippage_pct=1, commission_pct=1, tax_pct=1))
    portfolio.buy('OLD', 50, 10, '2020-06-01')
    cash = portfolio.cash
    costs = portfolio.get_cost_summary()
    portfolio.register_redemption('OLD', event, 50)
    assert 'OLD' not in portfolio.positions
    portfolio.process_redemptions('2020-07-08')
    assert portfolio.cash == cash
    assert portfolio.equity({}) == cash + 500
    portfolio.process_redemptions('2020-07-09')
    assert portfolio.cash == cash
    assert portfolio.equity({}) == cash + 400
    portfolio.process_redemptions('2020-07-10')
    assert portfolio.cash == cash + 400
    assert not portfolio.redemptions
    assert portfolio.get_cost_summary() == costs
    assert portfolio.trades[0].exit_price == 40


def test_missing_redemption_is_not_replaced_by_last_close():
    portfolio = Portfolio(1000)
    portfolio.buy('OLD', 50, 10, '2020-06-01')
    with pytest.raises(ValueError, match='Missing verified settlement'):
        portfolio.register_redemption('OLD', listing(), 50)
    assert 'OLD' in portfolio.positions


def test_zero_redemption_survives_csv_roundtrip(tmp_path):
    event = listing(valid_to='2020-06-30', settlement_amount=0,
                    settlement_date='2020-07-10', settlement_known_at='2020-07-09',
                    settlement_adjustment_factor=1, settlement_source='fixture')
    path = tmp_path / 'events.csv'
    ETFUniverse([event]).to_csv(path)
    assert ETFUniverse.from_csv(path).listings[0].settlement_amount == 0


def test_settlement_requires_adjustment_basis():
    with pytest.raises(ValueError, match='settlement evidence'):
        ETFUniverse([listing(valid_to='2020-06-30', settlement_amount=10)])


def test_price_evidence_must_match_input_rows():
    universe = ETFUniverse([listing()])
    prices = {'OLD': [{'date': '2020-01-01', 'close': 100}]}
    with pytest.raises(ValueError, match='required'):
        require_adjusted_price_evidence(universe, prices, None)
    evidence = {'tickers': {'OLD': dict(adjustment_verified=True, source='fixture prices',
                                      corporate_actions_source='fixture actions',
                                      prices_sha256=price_digest(prices['OLD']))}}
    require_adjusted_price_evidence(universe, prices, evidence)
    prices['OLD'][0]['close'] = 90
    with pytest.raises(ValueError, match='stale'):
        require_adjusted_price_evidence(universe, prices, evidence)


def test_engine_does_not_rebuy_delisted_ticker_and_waits_for_cash():
    dates = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(300)]
    event = listing(valid_to=dates[270], settlement_amount=350,
                    settlement_date=dates[280], settlement_known_at=dates[279],
                    settlement_adjustment_factor=1, settlement_source='fixture redemption')
    universe = ETFUniverse([event])
    engine = BacktestEngine(initial_capital=100000, commission_pct=0, slippage_pct=0, tax_pct=0)
    engine.add_price_data('OLD', [{'date': d, 'open': 100+i, 'close': 100+i}
                                  for i, d in enumerate(dates[:271])])
    engine.set_benchmark([{'date': d, 'close': 100} for d in dates])
    engine.run_adaptive_strategy('defensive_dual_momentum', dates[253], dates[-1],
                                 universe_history=universe, delisting_policy='verified')
    curve = {row['date']: row for row in engine.portfolio.equity_history}
    assert curve[dates[279]]['cash'] == curve[dates[271]]['cash']
    assert curve[dates[280]]['cash'] > curve[dates[279]]['cash']
    assert not engine.data_quality['deferred_orders']
    assert all('OLD' not in row['target_weights'] for row in engine.allocation_history
               if row['date'] > dates[270])
    assert all(trade.entry_date <= dates[270] for trade in engine.portfolio.trades)


def test_missing_pit_price_series_is_an_error():
    engine = BacktestEngine()
    with pytest.raises(ValueError, match='OLD'):
        engine.run_adaptive_strategy('defensive_dual_momentum', universe_history=ETFUniverse([listing()]))


def test_trading_stops_before_legal_delisting():
    event = listing(valid_to='2020-06-30', last_trading_date='2020-06-26',
                    trading_end_source='fixture trading halt')
    assert event.is_active_on('2020-06-26')
    assert not event.is_active_on('2020-06-29')
    assert not ETFUniverse([replace(event, last_trading_date=None)]).is_point_in_time_complete
