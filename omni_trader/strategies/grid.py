"""Grid trading bot — places a ladder of buy/sell orders around a base price.

Simplified backtest model: when price drops into the next grid line below,
buy a fixed fraction; when it rises into the next line above, sell. The engine
treats each grid fill as opening/closing a small unit position.
"""
from __future__ import annotations

from .base import Action, Context, Signal, Strategy


class GridBot(Strategy):
    def __init__(self, params: dict | None = None):
        super().__init__(params or {})
        self.grid_count = int(self.params.get("grid_count", 10))
        self.grid_gap_pct = float(self.params.get("grid_gap_pct", 0.01))
        self._lines = None
        self._last_idx = None

    def _build_grid(self, base: float):
        lines = [base * (1 + self.grid_gap_pct * (i - self.grid_count // 2))
                 for i in range(self.grid_count)]
        self._lines = sorted(lines)

    def on_bar(self, ctx: Context) -> Signal:
        i = ctx.index
        price = ctx.bars[i].close
        if self._lines is None:
            self._build_grid(price)
            return Signal(Action.HOLD)

        # find nearest grid line and direction of crossing
        # if price crossed below a line since last bar -> buy a grid unit
        # if crossed above a line -> sell a grid unit
        prev_price = ctx.bars[i - 1].close if i > 0 else price

        pos = ctx.position
        # Only one grid unit per position in this simplified model:
        # BUY when price < nearest lower line and we're flat,
        # SELL (short) when price > nearest upper line and we're flat,
        # CLOSE when price returns toward the midline.
        mid = self._lines[len(self._lines) // 2]

        if pos is None:
            if price < self._lines[1]:
                return Signal(Action.BUY, side="long", reason="grid buy lower band")
            if price > self._lines[-2]:
                return Signal(Action.SELL, side="short", reason="grid sell upper band")
            return Signal(Action.HOLD)

        if pos["side"] == "long" and price >= mid:
            return Signal(Action.CLOSE, reason="grid take-profit to mid")
        if pos["side"] == "short" and price <= mid:
            return Signal(Action.CLOSE, reason="grid take-profit to mid")
        return Signal(Action.HOLD)
