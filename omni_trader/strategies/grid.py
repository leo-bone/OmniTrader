"""Grid trading bot — a ladder of buy/sell levels around a moving anchor."""
from __future__ import annotations

from .base import Action, Context, Signal, Strategy
from .indicators import EMA, NAN


class GridBot(Strategy):
    """Ladder trades around an anchor price.

    Simplified backtest model (same as before): act when price sits in the
    lower/upper band of the ladder, exit when it returns to the midline.

    **Fix vs the original:** the original built its grid ONCE from the very
    first bar's close and never moved it. On any trending series the price
    walks straight out of the ladder after a few hundred bars and the bot goes
    permanently silent — which made it dead weight in every long backtest. The
    anchor now tracks an EMA of price (`anchor_bars`), so the ladder re-centres
    continuously. Set `anchor_bars <= 0` to restore the legacy fixed anchor.
    """

    def __init__(self, params: dict | None = None):
        super().__init__(params or {})
        self.grid_count = max(3, int(self.params.get("grid_count", 10)))
        self.grid_gap_pct = float(self.params.get("grid_gap_pct", 0.01))
        self.anchor_bars = int(self.params.get("anchor_bars", 100))
        self._reset_state()

    # ------------------------------------------------------------------
    def _reset_state(self) -> None:
        fixed = self.anchor_bars <= 0
        # legacy mode: latch the first price; otherwise follow an EMA
        self._fixed = fixed
        self._anchor_price: float | None = None
        self._ema = EMA(max(1, abs(self.anchor_bars))) if not fixed else None
        self._lines: list[float] | None = None
        self._last_index: int = -1

    def _build_grid(self, anchor: float) -> list[float]:
        half = self.grid_count // 2
        lines = [anchor * (1 + self.grid_gap_pct * (i - half))
                 for i in range(self.grid_count)]
        return sorted(lines)

    def on_bar(self, ctx: Context) -> Signal:
        i = ctx.index
        if i != self._last_index + 1:
            self._reset_state()
        self._last_index = i

        bar = ctx.bars[i]
        price = bar.close

        if self._fixed:
            if self._lines is None:
                self._lines = self._build_grid(price)
            lines = self._lines
        else:
            anchor = self._ema.update(price)
            if NAN == anchor:
                return Signal(Action.HOLD)
            lines = self._build_grid(anchor)

        if len(lines) < 3:  # need bands distinct from the midline
            return Signal(Action.HOLD)

        mid = lines[len(lines) // 2]
        pos = ctx.position

        if pos is None:
            if price < lines[1]:
                return Signal(Action.BUY, side="long", reason="grid buy lower band")
            if price > lines[-2]:
                return Signal(Action.SELL, side="short", reason="grid sell upper band")
            return Signal(Action.HOLD)

        if pos["side"] == "long" and price >= mid:
            return Signal(Action.CLOSE, reason="grid take-profit to mid")
        if pos["side"] == "short" and price <= mid:
            return Signal(Action.CLOSE, reason="grid take-profit to mid")
        return Signal(Action.HOLD)
