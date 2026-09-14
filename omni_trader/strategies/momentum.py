"""Momentum / breakout strategy with ADX trend filter (donchian + ADX)."""
from __future__ import annotations

from typing import List

from ..data import Bar
from .base import Action, Context, Signal, Strategy


class MomentumBreakout(Strategy):
    """Go long when price breaks above the N-bar high (and ADX confirms a
    trend); go short on breakdown below the N-bar low with ADX filter.
    Exit when price crosses back through the opposite break level.
    """

    def __init__(self, params: dict | None = None):
        super().__init__(params or {})
        self.lookback = int(self.params.get("lookback", 20))
        self.adx_period = int(self.params.get("adx_period", 14))
        self.adx_threshold = float(self.params.get("adx_threshold", 20.0))

    def on_bar(self, ctx: Context) -> Signal:
        i = ctx.index
        if i < self.lookback + self.adx_period:
            return Signal(Action.HOLD)

        closes = [b.close for b in ctx.bars]
        highs = [b.high for b in ctx.bars]
        lows = [b.low for b in ctx.bars]

        window_high = max(highs[i - self.lookback:i])
        window_low = min(lows[i - self.lookback:i])
        price = ctx.bars[i].close

        # ADX (directional strength) as a trend filter
        adx = self._adx(ctx.bars, self.adx_period)
        adx_now = adx[i] if i < len(adx) else float("nan")
        trending = adx_now == adx_now and adx_now >= self.adx_threshold  # nan-safe check

        pos = ctx.position
        if pos is None:
            if not trending:
                return Signal(Action.HOLD)
            if price > window_high:
                return Signal(Action.BUY, side="long", reason="breakout long + ADX")
            if price < window_low:
                return Signal(Action.SELL, side="short", reason="breakdown short + ADX")
            return Signal(Action.HOLD)

        # manage existing position
        entry = pos["entry"]
        if pos["side"] == "long" and price < window_low:
            return Signal(Action.CLOSE, reason="exit long on breakdown")
        if pos["side"] == "short" and price > window_high:
            return Signal(Action.CLOSE, reason="exit short on breakout")
        return Signal(Action.HOLD)

    @staticmethod
    def _adx(bars: List[Bar], period: int = 14) -> List[float]:
        if len(bars) < period + 1:
            return [float("nan")] * len(bars)
        plus_dm, minus_dm, tr = [], [], []
        for i in range(1, len(bars)):
            up = bars[i].high - bars[i - 1].high
            dn = bars[i - 1].low - bars[i].low
            plus_dm.append(max(up, 0.0) if up > dn else 0.0)
            minus_dm.append(max(dn, 0.0) if dn > up else 0.0)
            tr.append(max(bars[i].high - bars[i].low,
                          abs(bars[i].high - bars[i - 1].close),
                          abs(bars[i].low - bars[i - 1].close)))
        atr = sum(tr[:period]) / period
        pdi = 100 * sum(plus_dm[:period]) / period / atr if atr else 0.0
        mdi = 100 * sum(minus_dm[:period]) / period / atr if atr else 0.0
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if (pdi + mdi) else 0.0
        out = [float("nan")] * len(bars)
        adx_val = dx
        out[period] = adx_val
        for i in range(period + 1, len(bars)):
            atr = (atr * (period - 1) + tr[i - 1]) / period
            pdi = (pdi * (period - 1) + plus_dm[i - 1]) / period
            mdi = (mdi * (period - 1) + minus_dm[i - 1]) / period
            pdi_r = 100 * pdi / atr if atr else 0.0
            mdi_r = 100 * mdi / atr if atr else 0.0
            dx = 100 * abs(pdi_r - mdi_r) / (pdi_r + mdi_r) if (pdi_r + mdi_r) else 0.0
            adx_val = (adx_val * (period - 1) + dx) / period
            out[i] = adx_val
        return out
