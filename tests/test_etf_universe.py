from datetime import date, timedelta

import pytest

from adaptive_strategies import build_allocation_for_universe
from backtester import BacktestEngine
from etf_universe import ETFListing, ETFUniverse


def listing(**overrides):
    values = {
        'key': 'kr_equity',
        'ticker': 'OLD',
        'name': 'Old ETF',
        'asset_class': 'equity',
        'role': 'risk',
        'max_weight': 0.4,
        'valid_from': '2020-01-01',
        'coverage': 'point_in_time',
    }
    values.update(overrides)
    return ETFListing(**values)


def test_universe_selects_successor_by_rebalance_date():
    universe = ETFUniverse([
        listing(valid_to='2020-06-30', lifecycle_event='merged', successor_ticker='NEW'),
        listing(ticker='NEW', name='New ETF', valid_from='2020-07-01'),
    ])

    assert universe.active_on('2020-06-30')[0].ticker == 'OLD'
    assert universe.active_on('2020-07-01')[0].ticker == 'NEW'
    assert universe.risk_tickers('2020-07-01') == {'NEW'}


def test_universe_rejects_overlapping_lifecycle_rows():
    with pytest.raises(ValueError, match='overlapping'):
        ETFUniverse([
            listing(valid_to='2020-06-30'),
            listing(ticker='NEW', name='New ETF', valid_from='2020-06-30'),
        ])


def test_survivor_file_is_explicitly_not_pit_coverage():
    universe = ETFUniverse.from_csv('data/etf_universe_history.csv')

    assert universe.coverage == 'survivor_only'
    assert not universe.is_point_in_time_complete


def test_strategy_remaps_logical_slot_to_active_successor():
    active = [
        listing(ticker='NEW', name='New ETF', valid_from='2020-01-01'),
        listing(key='short_bond', ticker='153130', name='Cash ETF'),
    ]
    closes = {
        'NEW': [100 + i for i in range(260)],
        '153130': [100] * 260,
    }

    decision = build_allocation_for_universe('defensive_dual_momentum', closes, active)

    assert 'NEW' in decision.target_weights
    assert 'OLD' not in decision.target_weights


def test_pit_backtest_cash_liquidates_inactive_position():
    start = date(2020, 1, 1)
    dates = [(start + timedelta(days=i)).isoformat() for i in range(300)]
    old_end = dates[270]
    universe = ETFUniverse([
        listing(valid_to=old_end, lifecycle_event='delisted', successor_ticker='NEW'),
        listing(ticker='NEW', name='New ETF', valid_from=dates[271]),
    ])
    old_rows = [
        {'date': current, 'open': 100 + i, 'close': 100 + i}
        for i, current in enumerate(dates[:271])
    ]
    new_rows = [
        {'date': current, 'open': 200 + i, 'close': 200 + i}
        for i, current in enumerate(dates[271:])
    ]
    engine = BacktestEngine(initial_capital=1_000_000, commission_pct=0, slippage_pct=0, tax_pct=0)
    engine.add_price_data('OLD', old_rows, 'Old ETF')
    engine.add_price_data('NEW', new_rows, 'New ETF')
    engine.set_benchmark([{'date': current, 'close': 100} for current in dates])

    engine.run_adaptive_strategy(
        'defensive_dual_momentum',
        start_date=dates[253],
        end_date=dates[-1],
        base_risk_budget=1.0,
        universe_history=universe,
    )

    events = engine.data_quality['delisting_liquidations']
    assert events
    assert events[0]['ticker'] == 'OLD'
    assert engine.data_quality['universe_mode'] == 'point_in_time'
