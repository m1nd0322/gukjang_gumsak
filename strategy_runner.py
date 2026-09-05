from strategy_catalog import is_etf_strategy


def run_strategy(
    engine,
    key: str,
    tickers: list[str],
    start_date: str | None = None,
    end_date: str | None = None,
    stop_loss_pct: float = 7.0,
) -> None:
    if is_etf_strategy(key):
        engine.run_adaptive_strategy(key, start_date=start_date, end_date=end_date)
    elif key == "rebalance":
        engine.run_rebalance(tickers, start_date, end_date, period=20)
    elif key == "vol_trailing_stop":
        engine.run_volatility_trailing_stop(
            tickers,
            start_date,
            end_date,
            lookback=20,
            stop_pct=-10.0,
            cooldown=5,
            reentry=True,
        )
    elif key == "vol_trailing_stop_loss":
        engine.run_volatility_trailing_stop(
            tickers,
            start_date,
            end_date,
            lookback=20,
            stop_pct=-10.0,
            cooldown=5,
            reentry=True,
            stop_loss_pct=stop_loss_pct,
        )
    elif key == "ma_filter":
        engine.run_ma_filter(
            tickers,
            start_date,
            end_date,
            ma_period=20,
            rebalance_period=5,
        )
    elif key == "composite":
        engine.run_composite(
            tickers,
            start_date,
            end_date,
            ma_period=20,
            lookback=20,
            stop_pct=-8.0,
            cooldown=5,
            rebalance_period=10,
        )
    elif key == "equal_weight":
        engine.run_equal_weight(tickers, start_date, end_date)
    else:
        raise KeyError(key)
