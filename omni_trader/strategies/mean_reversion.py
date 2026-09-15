"""Mean-reversion strategy using Wilder RSI with Bollinger confirmation."""
from __future__ import annotations

from .base import Action, Context, Signal, Strategy
from .indicators import NAN, RSI, RollingWindow


class MeanReversion(Strategy):
    """Buy when RSI is oversold (< `oversold`); sell/short when overbought
    (> `overbought`). Optional Bollinger confirmation (`bb_confirm=True`)
    requires price to also sit outside the band — tighter but sparser.
    Exit when RSI returns to neutral (50) or the opposite extreme is hit.

    Streaming indicators keep this O(n) per backtest. See
    `omni_trader/strategies/indicators.py` for the RSI fix note: the old batch
    helper inverted RS past the seed window, which used to flip every signal
    after bar ~14.
    """

    def __init__(self, params: dict | None = None):
        super().__init__(params or {})
        self.rsi_period = int(self.params.get("rsi_period", 14))
        self.oversold = float(self.params.get("oversold", 30.0))
        self.overbought = float(self.params.get("overbought", 70.0))
        self.bb_period = int(self.params.get("bb_period", 20))
        self.bb_mult = float(self.params.get("bb_mult", 2.0))
        self.bb_confirm = bool(self.params.get("bb_confirm", False))
        self._reset_state()

    # ------------------------------------------------------------------
    def _reset_state(self) -> None:
        self._rsi = RSI(max(1, self.rsi_period))
        # the original window was `closes[i-bb_period:i+1]` -> size bb_period+1
        self._window = RollingWindow(max(1, self.bb_period) + 1)
        self._last_index: int = -1

    def on_bar(self, ctx: Context) -> Signal:
        i = ctx.index
        if i != self._last_index + 1:
            self._reset_state()
        self._last_index = i

        bar = ctx.bars[i]
        price = bar.close

        # RSI needs the current bar to yield its value at index i
        rsi_now = self._rsi.update(price)

        # Bollinger window includes the current bar
        below_bb = above_bb = False
        if self.bb_confirm:
            self._window.push(price)
            if self._window.ready:
                mid = self._window.mean()
                sd = self._window.std()
                below_bb = price < (mid - self.bb_mult * sd)
                above_bb = price > (mid + self.bb_mult * sd)

        if i < max(self.rsi_period, self.bb_period) + 1:
            return Signal(Action.HOLD)
        if NAN == rsi_now:
            return Signal(Action.HOLD)

        pos = ctx.position
        if pos is None:
            long_ok = rsi_now < self.oversold and (not self.bb_confirm or below_bb)
            short_ok = rsi_now > self.overbought and (not self.bb_confirm or above_bb)
            if long_ok:
                return Signal(Action.BUY, side="long", reason="RSI oversold")
            if short_ok:
                return Signal(Action.SELL, side="short", reason="RSI overbought")
            return Signal(Action.HOLD)

        if pos["side"] == "long" and rsi_now > 50:
            return Signal(Action.CLOSE, reason="reversion complete (long)")
        if pos["side"] == "short" and rsi_now < 50:
            return Signal(Action.CLOSE, reason="reversion complete (short)")
        return Signal(Action.HOLD)
