"""Exchange adapters: CEX live execution layer.

This layer is deliberately *safe by default*:
  - No adapter will place a real order unless explicitly put into live mode
    with credentials present.
  - All quantities use Decimal and are rounded to the exchange LOT_SIZE /
    amount precision before submission (fixes the float-drift reject bug).
  - Every order is gated through the RiskManager kill-switch.

The framework imports cleanly even if ``ccxt`` is not installed (the
Binance adapter imports it lazily).
"""

from .base import (
    ExchangeAdapter,
    OrderResult,
    Position,
    Balance,
    MarketInfo,
    TradeSide,
)
from .paper import PaperAdapter
from .binance import BinanceAdapter
from .live_engine import LiveTrader

__all__ = [
    "ExchangeAdapter",
    "OrderResult",
    "Position",
    "Balance",
    "MarketInfo",
    "TradeSide",
    "PaperAdapter",
    "BinanceAdapter",
    "LiveTrader",
]
