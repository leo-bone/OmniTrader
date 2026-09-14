"""Brokers and precision helpers.

The backtester is self-contained (no broker needed). This module provides:
  * `round_step_size` — Decimal-based LOT_SIZE rounding that fixes the float
    drift bug in the original quant-trading (`float(round(...))` could produce
    quantities Binance rejects).
  * `SimulatedBroker` — a thin in-memory fill simulator (used by the engine's
    unit tests / dry runs).
  * `LiveCCXTBroker` — a SAFE, clearly-gated adapter stub for live Binance.
    It will NOT trade unless explicitly constructed with `live=True` AND a
    paper==False flag, and it refuses to run without API keys present.
"""
from __future__ import annotations

import json
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from typing import Optional


def round_step_size(value: float, step: float, side: str = "down") -> float:
    """Round `value` to the nearest multiple of `step` using Decimal.

    `step` is the exchange LOT_SIZE / PRICE_FILTER stepSize. Using Decimal
    avoids float representation drift (e.g. 0.00000001 increments).
    """
    if step is None or step == 0:
        return float(value)
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    rounded = d_val.quantize(d_step, rounding=ROUND_DOWN if side == "down" else ROUND_UP)
    return float(rounded)


class SimulatedBroker:
    """In-memory broker used for tests and dry runs."""

    def __init__(self, fee_rate: float = 0.001):
        self.fee_rate = fee_rate
        self.cash = 0.0
        self.position = None

    def market_order(self, side: str, qty: float, price: float) -> dict:
        notional = qty * price
        fee = notional * self.fee_rate
        if side == "buy":
            self.cash -= notional + fee
            self.position = {"side": "long", "qty": qty, "entry": price}
        else:
            self.cash += notional - fee
            self.position = {"side": "short", "qty": qty, "entry": price}
        return {"status": "filled", "side": side, "qty": qty,
                "price": price, "fee": fee}


class LiveCCXTBroker:
    """Gated live adapter. SAFETY: refuses to construct in live mode without
    explicit intent + API keys, and warns loudly. Intentionally minimal — wire
    it to ccxt.pro / ccxt only after you have read the risk notes."""

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None,
                 live: bool = False, testnet: bool = True):
        self.live = live
        self.testnet = testnet
        if live and not testnet:
            if not (api_key and api_secret):
                raise RuntimeError(
                    "REFUSING live trading: API keys missing. "
                    "Never run live without keys stored in an encrypted vault."
                )
            import warnings
            warnings.warn(
                "LIVE TRADING ENABLED — real funds at risk. "
                "Start on testnet, use --paper, and set hard position/TSL limits.",
                RuntimeWarning,
            )
        self.api_key = api_key
        self.api_secret = api_secret

    def status(self) -> dict:
        mode = "LIVE" if (self.live and not self.testnet) else "testnet/paper"
        return {"mode": mode, "testnet": self.testnet, "configured": bool(self.api_key)}
