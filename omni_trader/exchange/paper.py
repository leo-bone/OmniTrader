"""In-process paper trading adapter.

Executes market fills against an internally tracked book so you can run the
same :class:`LiveTrader` loop in ``dry_run`` without any exchange. Uses Decimal
throughout. Refuses to operate if it would go negative (no infinite leverage).
"""
from __future__ import annotations

from decimal import Decimal

from .base import (
    ExchangeAdapter,
    OrderResult,
    Position,
    Balance,
    MarketInfo,
    TradeSide,
)


class PaperAdapter(ExchangeAdapter):
    mode = "paper"

    def __init__(self, starting_cash: float = 10_000.0, asset: str = "USDT"):
        self.asset = asset
        self.cash = Decimal(str(starting_cash))
        self.position = Position(symbol="", side="flat", qty=Decimal(0), entry_price=Decimal(0))
        self.last_price = Decimal(0)
        self._seq = 0

    # ---- interface -----------------------------------------------------
    def get_market(self, symbol: str) -> MarketInfo:
        return MarketInfo(
            symbol=symbol,
            price_precision=2,
            amount_precision=6,
            min_amount=Decimal("0.000001"),
        )

    def get_ticker(self, symbol: str) -> Decimal:
        return self.last_price

    def set_price(self, price: float) -> None:
        self.last_price = Decimal(str(price))

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=500):
        raise NotImplementedError("PaperAdapter has no historical feed; feed bars manually.")

    def get_balance(self, asset: str = "USDT") -> Balance:
        return Balance(total=self.cash, free=self.cash, asset=asset or self.asset)

    def get_position(self, symbol: str) -> Position:
        return self.position

    def place_order(self, symbol, side, qty, price=None, params=None) -> OrderResult:
        px = Decimal(str(price)) if price is not None else self.last_price
        if px <= 0:
            return OrderResult.fail("price must be > 0")
        qty = self.round_qty(self.get_market(symbol), Decimal(str(qty)))
        if qty <= 0:
            return OrderResult.fail("qty below minimum")
        notional = qty * px
        cur = self.position

        if side == TradeSide.BUY:
            if cur.side == "short":  # buy to cover
                cover = min(qty, cur.qty)
                self.cash += (cur.entry_price - px) * cover  # realize
                rem = cur.qty - cover
                if rem > 0:
                    self.position = Position(symbol, "short", rem, cur.entry_price)
                else:
                    leftover = qty - cover
                    if leftover > 0:  # flip to long
                        self.cash -= leftover * px
                        self.position = Position(symbol, "long", leftover, px)
                    else:
                        self.position = Position(symbol, "flat", Decimal(0), Decimal(0))
            elif cur.side == "long":  # add to long
                new_qty = cur.qty + qty
                new_entry = (cur.entry_price * cur.qty + px * qty) / new_qty
                self.cash -= notional
                self.position = Position(symbol, "long", new_qty, new_entry)
            else:  # flat -> open long
                if notional > self.cash:
                    return OrderResult.fail("insufficient cash for buy")
                self.cash -= notional
                self.position = Position(symbol, "long", qty, px)
        else:  # SELL
            if cur.side == "long":  # sell to close / reduce
                close = min(qty, cur.qty)
                self.cash += (px - cur.entry_price) * close  # realize
                rem = cur.qty - close
                if rem > 0:
                    self.position = Position(symbol, "long", rem, cur.entry_price)
                else:
                    leftover = qty - close
                    if leftover > 0:  # flip to short
                        self.position = Position(symbol, "short", leftover, px)
                        self.cash -= leftover * px  # reserve margin
                    else:
                        self.position = Position(symbol, "flat", Decimal(0), Decimal(0))
            elif cur.side == "short":  # add to short
                new_qty = cur.qty + qty
                new_entry = (cur.entry_price * cur.qty + px * qty) / new_qty
                self.position = Position(symbol, "short", new_qty, new_entry)
            else:  # flat -> open short
                self.position = Position(symbol, "short", qty, px)

        self._seq += 1
        return OrderResult(
            ok=True,
            order_id=f"paper-{self._seq}",
            filled_qty=qty,
            avg_price=px,
        )

    def cancel_order(self, order_id, symbol) -> OrderResult:
        return OrderResult(ok=True, order_id=order_id)

    def get_open_orders(self, symbol) -> list:
        return []

    # ---- helpers for equity ----------------------
    def equity(self) -> Decimal:
        if self.position.side == "long":
            return self.cash + self.position.qty * self.last_price
        if self.position.side == "short":
            # pnl = (entry - last) * qty ; cash already reduced by entry notional
            pnl = (self.position.entry_price - self.last_price) * self.position.qty
            return self.cash + pnl
        return self.cash
