import math
import pytest
from omni_trader.data import DataFeed, Bar
from omni_trader.backtest.engine import BacktestEngine
from omni_trader.risk import RiskConfig, RiskManager
from omni_trader.strategies.base import Strategy, Action, Signal


def make_feed(prices, ts_step=3600):
    bars = []
    ts = 1_700_000_000
    prev = prices[0]
    for i, c in enumerate(prices):
        o = prev
        h = max(o, c) * 1.001
        l = min(o, c) * 0.999
        bars.append(Bar(ts=ts + i * ts_step, open=o, high=h, low=l, close=c, volume=100))
        prev = c
    return DataFeed(symbol="TEST", timeframe="1h", bars=bars)


class BuyHoldThenExit(Strategy):
    """Buy at bar 1, exit at second-to-last bar. Deterministic for math tests."""
    def on_bar(self, ctx):
        if ctx.index == 1:
            return Signal(Action.BUY, side="long")
        if ctx.index == len(ctx.bars) - 2:
            return Signal(Action.CLOSE, reason="test exit")
        return Signal(Action.HOLD)


def test_equity_long_math():
    # prices rising; buy at 100, exit at 110; fee 0, slip 0 for clean check
    prices = [100] * 2 + [110] + [110] * 3
    feed = make_feed(prices)
    eng = BacktestEngine(feed, BuyHoldThenExit(), RiskConfig(), initial_capital=1000,
                         fee_rate=0.0, slippage=0.0)
    res = eng.run()
    # qty sized by risk: equity 1000, risk 1% = 10, stop default 5% of 100 =5 -> qty=2
    # entry ~100, exit ~110 -> pnl ~ 2*(110-100)=20 -> final ~1020
    assert res.num_trades == 1
    assert 1015 < res.final_equity < 1025
    assert res.total_return_pct > 0


def test_short_profits_when_price_falls():
    prices = [100] * 2 + [90] + [90] * 3

    class ShortStrat(Strategy):
        def on_bar(self, ctx):
            if ctx.index == 1:
                return Signal(Action.SELL, side="short")
            if ctx.index == len(ctx.bars) - 2:
                return Signal(Action.CLOSE)
            return Signal(Action.HOLD)

    feed = make_feed(prices)
    eng = BacktestEngine(feed, ShortStrat(), RiskConfig(), initial_capital=1000,
                         fee_rate=0.0, slippage=0.0)
    res = eng.run()
    assert res.final_equity > 1000  # short profited on drop
    assert res.total_return_pct > 0


def test_kill_switch_halts_on_daily_loss():
    cfg = RiskConfig(daily_loss_limit=0.02, max_drawdown_limit=0.99)
    rm = RiskManager(cfg)
    rm.update_equity(1000, 1_700_000_000)
    rm.record_realized_pnl(-30)  # 3% loss > 2% limit
    assert rm.halted is True
    assert rm.can_trade() is False


def test_no_lookahead_fill_next_bar():
    # signal at bar i must NOT use bar i+1 close; engine fills at i+1 open.
    # Construct a spike at bar 2 close and ensure ENTRY is bar 2 open (==bar1
    # close = 100), NOT the spiked 200 close. (The default 10% take-profit then
    # exits at 110 intrabar — correct, realistic behavior.)
    prices = [100, 100, 200, 200, 200, 200, 200]
    feed = make_feed(prices)
    res = BacktestEngine(feed, BuyHoldThenExit(), RiskConfig(),
                         initial_capital=1000, fee_rate=0.0, slippage=0.0).run()
    assert res.trades[0].entry <= 101   # filled at 100, not at the 200 spike
    assert res.trades[0].pnl > 0


def test_metrics_sanity():
    prices = [100 + i for i in range(50)]
    feed = make_feed(prices)
    res = BacktestEngine(feed, BuyHoldThenExit(), RiskConfig(),
                         initial_capital=1000, fee_rate=0.001, slippage=0.0005).run()
    assert math.isfinite(res.sharpe)
    assert 0 <= res.max_drawdown_pct <= 100
    assert res.num_trades == 1
