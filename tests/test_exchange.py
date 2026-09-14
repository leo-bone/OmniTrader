"""Tests for the CEX exchange layer (paper + live engine) — no network, no keys."""
from decimal import Decimal

from omni_trader.data import Bar, DataFeed
from omni_trader.exchange import (
    PaperAdapter,
    BinanceAdapter,
    LiveTrader,
    TradeSide,
    MarketInfo,
)
from omni_trader.strategies.base import Action, Context, Signal, Strategy
from omni_trader.risk import RiskConfig


def _bar(ts, o, h, l, c):
    return Bar(ts=ts, open=o, high=h, low=l, close=c, volume=10)


# ---- PaperAdapter -----------------------------------------------------
def test_paper_round_qty_respects_min():
    a = PaperAdapter(starting_cash=10_000)
    m = MarketInfo("BTCUSDT", amount_precision=6, min_amount=Decimal("0.0001"))
    assert a.round_qty(m, Decimal("0.00001")) == Decimal(0)  # below min
    assert a.round_qty(m, Decimal("0.000123456")) == Decimal("0.000123")


def test_paper_buy_and_equity():
    a = PaperAdapter(starting_cash=10_000)
    a.set_price(100)
    res = a.place_order("X", TradeSide.BUY, Decimal("0.1"), price=Decimal("100"))
    assert res.ok
    assert a.position.side == "long"
    # long held: cash 9990 + asset value 10 = 10000
    assert a.equity() == 10000.0


def test_paper_short_close_nets_out():
    a = PaperAdapter(starting_cash=10_000)
    a.set_price(100)
    a.place_order("X", TradeSide.SELL, Decimal("0.1"), price=Decimal("100"))  # open short
    assert a.position.side == "short"
    a.set_price(90)
    # buy to cover fully
    res = a.place_order("X", TradeSide.BUY, Decimal("0.1"), price=Decimal("90"))
    assert res.ok
    assert a.position.side == "flat"
    # pnl = (entry 100 - close 90) * 0.1 = 1.0 realized into cash
    assert a.equity() == 10000.0 + 1.0


# ---- BinanceAdapter safety --------------------------------------------
def test_binance_refuses_live_without_mode():
    b = BinanceAdapter()  # dry_run by default
    try:
        b.place_order("BTCUSDT", TradeSide.BUY, Decimal("0.01"))
        assert False, "should have raised"
    except RuntimeError as e:
        assert "dry_run" in str(e)


# ---- LiveTrader -------------------------------------------------------
class _FlipStrategy(Strategy):
    """Buy on bar 1, hold, close on the last bar."""

    def on_bar(self, ctx: Context) -> Signal:
        if ctx.index == 0:
            return Signal(Action.BUY, side="long")
        if ctx.index == len(ctx.bars) - 1:
            return Signal(Action.CLOSE, reason="eod")
        return Signal(Action.HOLD)


def test_live_trader_runs_on_paper_without_network():
    feed = DataFeed.generate_sample(symbol="BTCUSDT", n=200)
    adapter = PaperAdapter(starting_cash=10_000)
    trader = LiveTrader(
        _FlipStrategy(), adapter, RiskConfig(), initial_capital=10_000,
        dry_run=True, symbol="BTCUSDT",
    )
    for bar in feed.bars:
        trader.step(bar)
    assert trader.trades, "expected at least one open + close"
    # risk never halted on a gentle sample
    assert not trader.risk.halted
    assert trader._equity() > 0


def test_live_trader_halt_on_drawdown():
    # force a brutal drawdown by using a strategy that buys then price craters
    bars = [_bar(i, 100, 100, 100, 100) for i in range(5)]
    for i in range(5, 50):
        bars.append(_bar(i, 100 - i * 2, 100 - i * 2, 100 - i * 2, 100 - i * 2))
    feed = DataFeed("X", "1h", bars)
    adapter = PaperAdapter(starting_cash=10_000)
    trader = LiveTrader(
        _FlipStrategy(), adapter, RiskConfig(max_drawdown_limit=0.10),
        initial_capital=10_000, dry_run=True, symbol="X",
    )
    for bar in feed.bars:
        trader.step(bar)
    assert trader.risk.halted, "kill-switch should have fired on 10% drawdown"
    assert trader.risk.halt_reason is not None
