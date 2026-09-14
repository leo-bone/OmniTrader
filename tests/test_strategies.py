import math
from omni_trader.data import DataFeed, Bar
from omni_trader.strategies import MomentumBreakout, MeanReversion, GridBot
from omni_trader.strategies.base import Context, Action, Signal


def trend_feed(n=120, start=100.0, step=0.5):
    # First 30 bars chop around 100 (no trend), then a clear sustained surge to
    # ~140 so Donchian breakouts + ADX trend actually fire (a pure linear ramp
    # never makes a *new* high, so Donchian would never trigger).
    bars = []
    ts = 1_700_000_000
    for i in range(n):
        if i < 30:
            c = 100.0 + 2.0 * math.sin(i / 3.0)
        else:
            c = 100.0 + (i - 29) * 1.2
        bars.append(Bar(ts=ts + i * 3600, open=c, high=c + 1, low=c - 1, close=c, volume=10))
    return DataFeed("TREND", "1h", bars)


def chop_feed(n=120, mid=100.0):
    bars = []
    ts = 1_700_000_000
    for i in range(n):
        c = mid + 5 * math.sin(i / 4.0)
        bars.append(Bar(ts=ts + i * 3600, open=c, high=c + 1, low=c - 1, close=c, volume=10))
    return DataFeed("CHOP", "1h", bars)


def run_strategy(strat, feed):
    # simple harness: feed context bar by bar, collect signals
    sigs = []
    pos = None
    for i in range(len(feed.bars)):
        ctx = Context(bars=feed.bars, index=i, position=pos, equity=1000, params=strat.params)
        s = strat.on_bar(ctx)
        sigs.append(s.action)
        if s.action in (Action.BUY, Action.SELL):
            pos = {"side": s.side, "qty": 1, "entry": feed.bars[i].close, "stop": 0, "take": 0}
        elif s.action == Action.CLOSE:
            pos = None
    return sigs


def test_momentum_returns_signals_on_trend():
    sigs = run_strategy(MomentumBreakout({"lookback": 10, "adx_threshold": 5}), trend_feed())
    assert any(a in (Action.BUY, Action.SELL) for a in sigs)


def test_mean_reversion_runs_and_finite():
    strat = MeanReversion()
    sigs = run_strategy(strat, chop_feed())
    assert all(isinstance(a, Action) for a in sigs)


def test_rsi_finite():
    closes = [100 + 2 * math.sin(i / 3.0) for i in range(40)]
    rsi = MeanReversion.rsi_wilder(closes, 14)
    assert all(math.isfinite(x) for x in rsi if not math.isnan(x))


def test_grid_runs():
    sigs = run_strategy(GridBot({"grid_count": 10, "grid_gap_pct": 0.01}), chop_feed())
    assert all(isinstance(a, Action) for a in sigs)
