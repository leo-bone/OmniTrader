"""Mean-reversion strategy using Wilder RSI with Bollinger confirmation."""
from __future__ import annotations

from .base import Action, Context, Signal, Strategy


class MeanReversion(Strategy):
    """Buy when RSI(14) is oversold (< 30); sell/short when overbought (> 70).
    Optional Bollinger confirmation (`bb_confirm=True`) requires price to also
    be outside the band — tighter but sparser. Exit when RSI returns to neutral
    (50) or the opposite extreme is hit.
    """

    def __init__(self, params: dict | None = None):
        super().__init__(params or {})
        self.rsi_period = int(self.params.get("rsi_period", 14))
        self.oversold = float(self.params.get("oversold", 30.0))
        self.overbought = float(self.params.get("overbought", 70.0))
        self.bb_period = int(self.params.get("bb_period", 20))
        self.bb_mult = float(self.params.get("bb_mult", 2.0))
        self.bb_confirm = bool(self.params.get("bb_confirm", False))

    def on_bar(self, ctx: Context) -> Signal:
        i = ctx.index
        if i < max(self.rsi_period, self.bb_period) + 1:
            return Signal(Action.HOLD)

        closes = [b.close for b in ctx.bars]
        rsi = self.rsi_wilder(closes, self.rsi_period)
        rsi_now = rsi[i]
        price = ctx.bars[i].close

        below_bb = above_bb = False
        if self.bb_confirm:
            window = closes[i - self.bb_period:i + 1]
            mid = sum(window) / len(window)
            var = sum((x - mid) ** 2 for x in window) / len(window)
            sd = var ** 0.5
            below_bb = price < (mid - self.bb_mult * sd)
            above_bb = price > (mid + self.bb_mult * sd)

        pos = ctx.position
        if pos is None:
            long_ok = rsi_now < self.oversold and (not self.bb_confirm or below_bb)
            short_ok = rsi_now > self.overbought and (not self.bb_confirm or above_bb)
            if long_ok:
                return Signal(Action.BUY, side="long", reason="RSI oversold")
            if short_ok:
                return Signal(Action.SELL, side="short", reason="RSI overbought")
            return Signal(Action.HOLD)

        # exit: mean reverted
        if pos["side"] == "long" and (rsi_now > 50):
            return Signal(Action.CLOSE, reason="reversion complete (long)")
        if pos["side"] == "short" and (rsi_now < 50):
            return Signal(Action.CLOSE, reason="reversion complete (short)")
        return Signal(Action.HOLD)
