"""Abstract exchange interface + shared value objects."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional


class TradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class OrderResult:
    ok: bool
    order_id: Optional[str] = None
    filled_qty: Decimal = Decimal(0)
    avg_price: Decimal = Decimal(0)
    error: Optional[str] = None

    @classmethod
    def fail(cls, error: str) -> "OrderResult":
        return cls(ok=False, error=error)


@dataclass
class Position:
    symbol: str
    side: str  # "long" | "short" | "flat"
    qty: Decimal = Decimal(0)
    entry_price: Decimal = Decimal(0)
    unrealized_pnl: Decimal = Decimal(0)

    @property
    def is_open(self) -> bool:
        return self.side != "flat" and self.qty > 0


@dataclass
class Balance:
    total: Decimal = Decimal(0)
    free: Decimal = Decimal(0)
    used: Decimal = Decimal(0)
    asset: str = "USDT"


@dataclass
class MarketInfo:
    symbol: str
    price_precision: int = 2
    amount_precision: int = 6
    min_amount: Decimal = Decimal(0)
    min_notional: Decimal = Decimal(0)
    tick_size: Decimal = Decimal(0)
    lot_size: Decimal = Decimal(0)


class ExchangeAdapter(ABC):
    """Common interface every exchange (or paper simulator) implements."""

    mode: str = "dry_run"  # "dry_run" | "paper" | "live"

    @abstractmethod
    def get_market(self, symbol: str) -> MarketInfo:
        ...

    @abstractmethod
    def get_ticker(self, symbol: str) -> Decimal:
        """Last price."""

    @abstractmethod
    def fetch_ohlcv(
        self, symbol: str, timeframe: str = "1h", limit: int = 500
    ) -> list:
        """Return list of Bars (see omni_trader.data.Bar)."""

    @abstractmethod
    def get_balance(self, asset: str = "USDT") -> Balance:
        ...

    @abstractmethod
    def get_position(self, symbol: str) -> Position:
        ...

    @abstractmethod
    def place_order(
        self,
        symbol: str,
        side: TradeSide,
        qty: Decimal,
        price: Optional[Decimal] = None,
        params: Optional[dict] = None,
    ) -> OrderResult:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str) -> OrderResult:
        ...

    @abstractmethod
    def get_open_orders(self, symbol: str) -> list:
        ...

    # ---- shared helpers -------------------------------------------------
    def round_qty(self, market: MarketInfo, qty: Decimal) -> Decimal:
        """Round to amount precision and enforce LOT_SIZE step + min."""
        from decimal import ROUND_DOWN

        q = qty.quantize(
            Decimal(10) ** -market.amount_precision, rounding=ROUND_DOWN
        )
        if market.lot_size > 0:
            q = (q / market.lot_size).to_integral_value(ROUND_DOWN) * market.lot_size
            q = q.quantize(Decimal(10) ** -market.amount_precision, ROUND_DOWN)
        if q < market.min_amount:
            return Decimal(0)
        return q

    def round_price(self, market: MarketInfo, price: Decimal) -> Decimal:
        from decimal import ROUND_HALF_UP

        return price.quantize(
            Decimal(10) ** -market.price_precision, rounding=ROUND_HALF_UP
        )
