"""Momentum / breakout strategy with ADX trend filter (Donchian + ADX)."""
from __future__ import annotations

from typing import List

from ..data import Bar
from .base import Action, Context, Signal, Strategy
from .indicators import ADX, NAN, RollingExtremes


class MomentumBreakout(Strategy):
    """Go long when price breaks above the N-bar high (with ADX confirming a
    trend); go short on a breakdown below the N-bar low, same filter.
    Exit when price crosses back through the opposite break level.

    Uses streaming indicators (see `omni_trader/strategies/indicators.py`) so
    the whole backtest is O(n) instead of the O(n^2) of the original
    batch-recompute version. Decision logic is unchanged.
    """

    def __init__(self, params: dict | None = None):
        super().__init__(params or {})
        self.lookback = int(self.params.get("lookback", 20))
        self.adx_period = int(self.params.get("adx_period", 14))
        self.adx_threshold = float(self.params.get("adx_threshold", 20.0))
        self._reset_state()

    # ------------------------------------------------------------------
    def _reset_state(self) -> None:
        self._highs = RollingExtremes(max(1, self.lookback))
        self._lows = RollingExtremes(max(1, self.lookback))
        self._adx = ADX(max(1, self.adx_period))
        self._last_index: int = -1

    def on_bar(self, ctx: Context) -> Signal:
        i = ctx.index
        if i != self._last_index + 1:
            # reused instance / new series — never carry stale indicator state
            self._reset_state()
        self._last_index = i

        bar = ctx.bars[i]
        # (1) window extremes over bars [i-lookback, i) — current bar excluded,
        #     exactly like the original `highs[i-lookback:i]` slice.
        window_high = self._highs.max()
        window_low = self._lows.min()
        # (2) ADX needs the CURRENT bar to produce its value at index i
        adx_now = self._adx.update(bar.high, bar.low, bar.close)
        # (3) book the current bar for future windows
        self._highs.push(bar.high)
        self._lows.push(bar.low)

        if i < self.lookback + self.adx_period:
            return Signal(Action.HOLD)

        nanish = (NAN == adx_now) or (NAN == window_high)  # NaN-safe checks
        if nanish:
            return Signal(Action.HOLD)

        trending = adx_now >= self.adx_threshold
        price = bar.close
        pos = ctx.position

        if pos is None:
            if not trending:
                return Signal(Action.HOLD)
            if price > window_high:
                return Signal(Action.BUY, side="long", reason="breakout long + ADX")
            if price < window_low:
                return Signal(Action.SELL, side="short", reason="breakdown short + ADX")
            return Signal(Action.HOLD)

        if pos["side"] == "long" and price < window_low:
            return Signal(Action.CLOSE, reason="exit long on breakdown")
        if pos["side"] == "short" and price > window_high:
            return Signal(Action.CLOSE, reason="exit short on breakout")
        return Signal(Action.HOLD)

    # ------------------------------------------------------------------
    @staticmethod
    def _adx(bars: List[Bar], period: int = 14) -> List[float]:
        """Batch reference implementation (unchanged semantics, O(n)).

        Kept for cross-checks and for callers that want a whole series at once;
        the live strategy path uses the streaming `ADX` class instead.
        """
        if len(bars) < period + 1:
            return [NAN] * len(bars)
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
        out = [NAN] * len(bars)
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
