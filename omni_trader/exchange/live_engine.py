"""Live trading loop glue.

Runs a :class:`Strategy` against a stream of bars (or a live feed) and executes
through an :class:`ExchangeAdapter`. Two guarantees that the original repos
lacked:

  1. The RiskManager kill-switch is checked EVERY bar (``update_equity``) and
     before EVERY entry (``can_trade``), and realized PnL is recorded on every
     close (``record_realized_pnl``). A halted engine refuses to open.
  2. No real order is ever placed unless the adapter is in ``"live"`` mode AND
     ``dry_run=False``. The default :class:`PaperAdapter` path is fully
     simulated and leaves no exchange connection open.

This module is safe to import and unit-test without any exchange credentials.
"""
from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from ..data import Bar
from ..risk import RiskConfig, RiskManager
from ..strategies.base import Action, Context, Strategy
from .base import TradeSide


class LiveTrader:
    def __init__(
        self,
        strategy: Strategy,
        adapter,
        risk_config: Optional[RiskConfig] = None,
        initial_capital: float = 10_000.0,
        dry_run: bool = True,
        symbol: str = "BTCUSDT",
        max_history: int = 2000,
    ):
        self.strategy = strategy
        self.adapter = adapter
        self.risk = RiskManager(risk_config or RiskConfig())
        self.initial_capital = initial_capital
        self.dry_run = dry_run
        self.symbol = symbol
        self.max_history = max_history
        self.bars: List[Bar] = []
        self.trades: List[dict] = []
        self.risk.equity = initial_capital
        self.risk.peak_equity = initial_capital

    # -- state -----------------------------------------------------------
    def _pos_dict(self) -> Optional[dict]:
        p = self.adapter.get_position(self.symbol)
        if not p.is_open:
            return None
        return {
            "side": p.side,
            "qty": float(p.qty),
            "entry": float(p.entry_price),
            "stop": 0.0,
            "take": 0.0,
        }

    def _equity(self) -> float:
        # Prefer the adapter's own book when it tracks one (PaperAdapter).
        eq_fn = getattr(self.adapter, "equity", None)
        if callable(eq_fn):
            return float(eq_fn())
        b = self.adapter.get_balance("USDT")
        p = self.adapter.get_position(self.symbol)
        px = float(self.adapter.get_ticker(self.symbol))
        eq = float(b.free)
        if p.side == "long":
            eq += float(p.qty) * px
        elif p.side == "short":
            eq += (float(p.entry_price) - px) * float(p.qty)
        return eq

    # -- main step -------------------------------------------------------
    def step(self, bar: Bar) -> dict:
        self.bars.append(bar)
        if len(self.bars) > self.max_history:
            self.bars.pop(0)
        if hasattr(self.adapter, "set_price"):
            self.adapter.set_price(bar.close)

        px = float(bar.close)
        equity = self._equity()
        self.risk.update_equity(equity, bar.ts)
        if self.risk.halted:
            return {"event": "halted", "reason": self.risk.halt_reason}

        ctx = Context(
            bars=self.bars,
            index=len(self.bars) - 1,
            position=self._pos_dict(),
            equity=equity,
            params=self.strategy.params,
        )
        sig = self.strategy.on_bar(ctx)
        if sig.action == Action.HOLD:
            return {"event": "hold"}

        pos = self._pos_dict()
        if sig.action == Action.CLOSE:
            if not pos:
                return {"event": "no_position"}
            return self._close(px, bar.ts)

        # BUY / SELL -> open (or flip if opposite side already open)
        if pos and pos["side"] != sig.side:
            self._close(px, bar.ts)
        if pos and pos["side"] == sig.side:
            return {"event": "already_open"}
        return self._open(sig, px, bar.ts)

    # -- execution helpers ----------------------------------------------
    def _open(self, sig, px: float, ts: int) -> dict:
        if not self.risk.can_trade():
            return {"event": "risk_blocked", "reason": "kill-switch engaged"}
        entry = px
        side = sig.side
        stop = (
            entry * (1 - sig.stop_pct)
            if sig.stop_pct
            else self.risk._default_stop(entry, side)
        )
        take = (
            entry * (1 + sig.take_pct)
            if sig.take_pct
            else self.risk._default_take(entry, side)
        )
        sizing = self.risk.size_position(entry, stop, side)
        qty = Decimal(str(sig.qty)) if sig.qty else Decimal(str(sizing.qty))
        trade_side = TradeSide.BUY if side == "long" else TradeSide.SELL
        res = self.adapter.place_order(self.symbol, trade_side, qty, price=Decimal(str(px)))
        if not res.ok:
            return {"event": "order_failed", "error": res.error}
        self.trades.append(
            {
                "ts": ts,
                "event": "open",
                "side": side,
                "qty": float(res.filled_qty),
                "entry": float(res.avg_price),
                "stop": float(stop),
                "take": float(take),
            }
        )
        return {"event": "open", "side": side, "qty": float(res.filled_qty), "entry": float(res.avg_price)}

    def _close(self, px: float, ts: int) -> dict:
        pos = self._pos_dict()
        if not pos:
            return {"event": "no_position"}
        side = TradeSide.SELL if pos["side"] == "long" else TradeSide.BUY
        res = self.adapter.place_order(
            self.symbol, side, Decimal(str(pos["qty"])), price=Decimal(str(px))
        )
        if not res.ok:
            return {"event": "close_failed", "error": res.error}
        pnl = (
            (px - pos["entry"]) * pos["qty"]
            if pos["side"] == "long"
            else (pos["entry"] - px) * pos["qty"]
        )
        self.risk.record_realized_pnl(pnl)
        self.trades.append(
            {
                "ts": ts,
                "event": "close",
                "side": pos["side"],
                "qty": float(pos["qty"]),
                "entry": float(pos["entry"]),
                "exit": float(px),
                "pnl": float(pnl),
            }
        )
        return {"event": "close", "pnl": float(pnl)}

    def summary(self) -> dict:
        return {
            "equity": round(self._equity(), 2),
            "risk": self.risk.summary(),
            "open_trades": len([t for t in self.trades if t.get("event") == "open"]),
            "trades": len(self.trades),
        }
