"""Risk manager — position sizing, stop/take, and LIVE-WIRED kill switches.

The original repos (quant-trading, QuantAgent) had a risk manager whose
`update_balance()` / `record_pnl()` were never called in the live loop, so
daily-loss and drawdown halts could NEVER fire. Here the engine MUST call
`update_equity()` and `record_realized_pnl()` every bar/exit, and the engine
checks `can_trade()` before every entry. This is the single most important
safety fix in the consolidation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RiskConfig:
    # position sizing
    risk_per_trade: float = 0.01      # fraction of equity risked per trade
    max_position_pct: float = 0.30    # cap: never exceed 30% of equity in one position
    # protective levels (as fraction of entry)
    default_stop_pct: float = 0.05
    default_take_pct: float = 0.10
    # kill switches — wired into the engine
    daily_loss_limit: float = 0.05    # halt if realized loss for the day >= 5% of equity
    max_drawdown_limit: float = 0.20  # halt if equity drawdown from peak >= 20%
    # leverage guard (informational; live adapters should enforce)
    max_leverage: float = 1.0


@dataclass
class PositionSizing:
    qty: float
    stop_price: float
    take_price: float


class RiskManager:
    def __init__(self, config: Optional[RiskConfig] = None):
        self.cfg = config or RiskConfig()
        self.equity = 0.0
        self.peak_equity = 0.0
        self.daily_realized_pnl = 0.0
        self._day_key: Optional[int] = None  # YYYYMMDD
        self.halted = False
        self.halt_reason: Optional[str] = None

    # ------------------------------------------------------------------
    # State updates — the engine MUST call these every bar / on every exit.
    # ------------------------------------------------------------------
    def update_equity(self, equity: float, ts: int) -> None:
        self.equity = equity
        self.peak_equity = max(self.peak_equity, equity)
        day_key = ts // 86400
        if self._day_key is None:
            self._day_key = day_key
        if day_key != self._day_key:
            # new UTC day -> reset daily pnl tracker
            self._day_key = day_key
            self.daily_realized_pnl = 0.0
        self._check_halts()

    def record_realized_pnl(self, pnl: float) -> None:
        self.daily_realized_pnl += pnl
        self._check_halts()

    def _check_halts(self) -> None:
        if self.halted:
            return
        if self.equity <= 0:
            self.halted = True
            self.halt_reason = "equity <= 0"
            return
        if self.daily_realized_pnl <= -self.cfg.daily_loss_limit * self.equity:
            self.halted = True
            self.halt_reason = (
                f"daily loss limit hit: {self.daily_realized_pnl:.2f} "
                f"<= -{self.cfg.daily_loss_limit*100:.0f}% of equity"
            )
            return
        if self.equity <= self.peak_equity * (1 - self.cfg.max_drawdown_limit):
            self.halted = True
            self.halt_reason = (
                f"max drawdown limit hit: dd={(1-self.equity/self.peak_equity)*100:.1f}% "
                f">= {self.cfg.max_drawdown_limit*100:.0f}%"
            )

    def can_trade(self) -> bool:
        """Called by the engine before EVERY new entry. This is what was
        missing in the original repos — entering while halted is blocked."""
        return not self.halted

    def drawdown(self) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, 1 - self.equity / self.peak_equity)

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------
    def size_position(
        self,
        entry_price: float,
        stop_price: Optional[float] = None,
        side: str = "long",
    ) -> PositionSizing:
        stop = stop_price if stop_price is not None else self._default_stop(entry_price, side)
        take = self._default_take(entry_price, side)
        if side == "long":
            stop_dist = max(entry_price - stop, 1e-9)
        else:
            stop_dist = max(stop - entry_price, 1e-9)
        risk_capital = self.equity * self.cfg.risk_per_trade
        qty = risk_capital / stop_dist
        # cap by max_position_pct of equity
        max_qty = (self.equity * self.cfg.max_position_pct) / entry_price
        qty = min(qty, max_qty)
        return PositionSizing(qty=qty, stop_price=stop, take_price=take)

    def _default_stop(self, entry: float, side: str) -> float:
        s = self.cfg.default_stop_pct
        return entry * (1 - s) if side == "long" else entry * (1 + s)

    def _default_take(self, entry: float, side: str) -> float:
        t = self.cfg.default_take_pct
        return entry * (1 + t) if side == "long" else entry * (1 - t)

    def summary(self) -> dict:
        return {
            "equity": round(self.equity, 2),
            "peak_equity": round(self.peak_equity, 2),
            "drawdown": round(self.drawdown() * 100, 2),
            "daily_realized_pnl": round(self.daily_realized_pnl, 2),
            "halted": self.halted,
            "halt_reason": self.halt_reason,
        }
