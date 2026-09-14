"""Binance CEX adapter (ccxt). Live trading only with explicit keys.

Safety:
  - ``mode`` defaults to ``"dry_run"``. Calling :meth:`place_order` in any mode
    other than ``"live"`` raises. Set ``mode="live"`` only after you have set
    ``BINANCE_API_KEY`` / ``BINANCE_SECRET`` (or pass them).
  - Quantities are rounded to the symbol's LOT_SIZE / amount precision and the
    min-notional is enforced (fixes the float-drift "MIN_NOTIONAL" reject bug).
  - Uses Spot by default. Futures positions are read when ``futures=True``.
"""
from __future__ import annotations

import os
from decimal import Decimal

from .base import (
    ExchangeAdapter,
    OrderResult,
    Position,
    Balance,
    MarketInfo,
    TradeSide,
)


class BinanceAdapter(ExchangeAdapter):
    mode = "dry_run"

    def __init__(self, api_key=None, secret=None, testnet: bool = True, futures: bool = False):
        self.api_key = api_key or os.environ.get("BINANCE_API_KEY")
        self.secret = secret or os.environ.get("BINANCE_SECRET")
        self.testnet = testnet
        self.futures = futures
        self._client = None
        self._markets: dict = {}

    # -- client ----------------------------------------------------------
    def _client(self):
        if self._client is None:
            import ccxt  # lazy

            cls = ccxt.binance
            self._client = cls(
                {
                    "apiKey": self.api_key,
                    "secret": self.secret,
                    "enableRateLimit": True,
                    "options": {"defaultType": "future" if self.futures else "spot"},
                }
            )
            if self.testnet and not self.futures:
                self._client.set_sandbox_mode(True)
        return self._client

    def _require_live(self):
        if self.mode != "live":
            raise RuntimeError(
                "BinanceAdapter is in dry_run. Set mode='live' with API keys to trade."
            )
        if not (self.api_key and self.secret):
            raise RuntimeError("Live mode requires BINANCE_API_KEY and BINANCE_SECRET.")

    # -- market info -----------------------------------------------------
    def get_market(self, symbol: str) -> MarketInfo:
        if symbol not in self._markets:
            m = self._client().market(symbol)
            prec = m.get("precision", {})
            limits = m.get("limits", {})
            amount_prec = int(prec.get("amount", 8))
            price_prec = int(prec.get("price", 2))
            filters = {f.get("filterType"): f for f in m.get("info", {}).get("filters", [])}
            lot = Decimal(str(filters.get("LOT_SIZE", {}).get("stepSize", "0")))
            min_amount = Decimal(str(limits.get("amount", {}).get("min", "0") or "0"))
            min_notional = Decimal(str(filters.get("MIN_NOTIONAL", {}).get("minNotional", "0") or "0"))
            tick = Decimal(str(filters.get("PRICE_FILTER", {}).get("tickSize", "0") or "0"))
            self._markets[symbol] = MarketInfo(
                symbol=symbol,
                price_precision=price_prec,
                amount_precision=amount_prec,
                min_amount=min_amount,
                min_notional=min_notional,
                tick_size=tick,
                lot_size=lot,
            )
        return self._markets[symbol]

    # -- market data -----------------------------------------------------
    def get_ticker(self, symbol: str) -> Decimal:
        t = self._client().fetch_ticker(symbol)
        return Decimal(str(t["last"]))

    def fetch_ohlcv(self, symbol, timeframe="1h", limit=500):
        from omni_trader.data import Bar

        rows = self._client().fetch_ohlcv(symbol, timeframe, limit=limit)
        return [
            Bar(ts=int(r[0]) // 1000, open=r[1], high=r[2], low=r[3], close=r[4], volume=r[5])
            for r in rows
        ]

    def get_balance(self, asset: str = "USDT") -> Balance:
        b = self._client().fetch_balance()
        free = Decimal(str(b.get(asset, {}).get("free", 0)))
        total = Decimal(str(b.get(asset, {}).get("total", free)))
        return Balance(total=total, free=free, used=total - free, asset=asset)

    def get_position(self, symbol: str) -> Position:
        if not self.futures:
            # spot: no open position concept; report flat
            return Position(symbol, "flat", Decimal(0), Decimal(0))
        pos = self._client().fetch_positions([symbol])
        p = next((x for x in pos if x.get("symbol") == symbol), None)
        if not p or not p.get("contracts"):
            return Position(symbol, "flat", Decimal(0), Decimal(0))
        side = "long" if float(p["contracts"]) > 0 else "short"
        return Position(
            symbol,
            side,
            Decimal(str(abs(float(p["contracts"])))),
            Decimal(str(p.get("entryPrice", 0) or 0)),
        )

    # -- orders ----------------------------------------------------------
    def place_order(self, symbol, side, qty, price=None, params=None) -> OrderResult:
        self._require_live()
        market = self.get_market(symbol)
        qty = self.round_qty(market, Decimal(str(qty)))
        if qty <= 0:
            return OrderResult.fail("qty below LOT_SIZE / min_amount")
        notional = qty * (price or self.get_ticker(symbol))
        if market.min_notional and notional < market.min_notional:
            return OrderResult.fail(f"notional {notional} < min_notional {market.min_notional}")
        try:
            if price is None:
                o = self._client().create_market_order(symbol, side.value, float(qty))
            else:
                o = self._client().create_limit_order(
                    symbol, side.value, float(qty), float(price)
                )
            return OrderResult(
                ok=True,
                order_id=str(o.get("id")),
                filled_qty=Decimal(str(o.get("filled", qty))),
                avg_price=Decimal(str(o.get("average") or o.get("price") or price or 0)),
            )
        except Exception as e:  # surface exchange errors instead of silently failing
            return OrderResult.fail(f"{type(e).__name__}: {e}")

    def cancel_order(self, order_id, symbol) -> OrderResult:
        self._require_live()
        try:
            self._client().cancel_order(order_id, symbol)
            return OrderResult(ok=True, order_id=order_id)
        except Exception as e:
            return OrderResult.fail(str(e))

    def get_open_orders(self, symbol) -> list:
        self._require_live()
        return self._client().fetch_open_orders(symbol)
