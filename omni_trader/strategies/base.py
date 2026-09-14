"""Strategy interface and signal types."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

from ..data import Bar


class Action(str, Enum):
    BUY = "BUY"        # open / add long
    SELL = "SELL"      # open short
    CLOSE = "CLOSE"    # flatten current position
    HOLD = "HOLD"


@dataclass
class Signal:
    action: Action
    side: str = "long"                 # long | short (for BUY/SELL)
    qty: Optional[float] = None        # None -> risk manager sizes it
    stop_pct: Optional[float] = None   # override risk default stop (fraction)
    take_pct: Optional[float] = None   # override risk default take (fraction)
    reason: str = ""


@dataclass
class Context:
    """Everything a strategy is allowed to see at bar `i` (no future data)."""
    bars: List[Bar]          # bars[0..i] inclusive — NEVER bars[i+1..]
    index: int
    position: Optional[dict]  # current open position or None
    equity: float
    params: dict = field(default_factory=dict)


class Strategy(ABC):
    """Subclass and implement `on_bar`. Return one Signal per bar.

    IMPORTANT (fixes the lookahead bias in the original repos): the engine
    only calls `on_bar` with data up to the current bar and executes the
    returned signal at the NEXT bar's open. So you may freely use
    `ctx.bars[-1].close` (the current close) — the fill happens later.
    """

    def __init__(self, params: Optional[dict] = None):
        self.params = params or {}

    @abstractmethod
    def on_bar(self, ctx: Context) -> Signal:
        ...

    # ---- small shared indicator helpers (Wilder RSI, EMA, ATR) ----
    @staticmethod
    def ema(values: List[float], period: int) -> List[float]:
        if not values:
            return []
        k = 2.0 / (period + 1)
        out = [values[0]]
        for v in values[1:]:
            out.append(v * k + out[-1] * (1 - k))
        return out

    @staticmethod
    def rsi_wilder(closes: List[float], period: int = 14) -> List[float]:
        if len(closes) <= period:
            return [float("nan")] * len(closes)
        gains, losses = [], []
        for i in range(1, len(closes)):
            ch = closes[i] - closes[i - 1]
            gains.append(max(ch, 0.0))
            losses.append(max(-ch, 0.0))
        avg_g = sum(gains[:period]) / period
        avg_l = sum(losses[:period]) / period
        out = [float("nan")] * period
        out.append(100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l))
        for i in range(period + 1, len(closes)):
            avg_g = (avg_g * (period - 1) + gains[i - 1]) / period
            avg_l = (avg_l * (period - 1) + losses[i - 1]) / period
            out.append(100.0 if avg_l == 0 else 100 - 100 / (1 + avg_l / avg_g))
        return out

    @staticmethod
    def atr(bars: List[Bar], period: int = 14) -> List[float]:
        if len(bars) < 2:
            return [float("nan")] * len(bars)
        tr = []
        for i in range(1, len(bars)):
            h, l, pc = bars[i].high, bars[i].low, bars[i - 1].close
            tr.append(max(h - l, abs(h - pc), abs(l - pc)))
        out = [float("nan")] * len(bars)
        if len(tr) < period:
            return out
        avg = sum(tr[:period]) / period
        out[period] = avg  # aligns with bar index `period`
        for i in range(period + 1, len(bars)):
            avg = (avg * (period - 1) + tr[i - 1]) / period
            out[i] = avg
        return out
