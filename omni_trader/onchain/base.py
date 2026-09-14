"""On-chain abstractions."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


@dataclass
class Quote:
    input_mint: str
    output_mint: str
    in_amount: Decimal
    out_amount: Decimal
    price_impact_pct: Decimal
    raw: dict = None  # original response for downstream swap building


class Signer(ABC):
    """Owns a private key and signs serialized transactions.

    Implementations must NEVER expose the raw secret through ``__str__`` /
    ``__repr__`` or logs.
    """

    @abstractmethod
    def public_key(self) -> str:
        ...

    @abstractmethod
    def sign_raw(self, payload: bytes) -> bytes:
        """Sign a serialized transaction, return signature bytes."""


class OnChainExecutor(ABC):
    """Common interface for a chain swap client."""

    chain: str = "unknown"
    simulation: bool = True

    @abstractmethod
    def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: Decimal,
        slippage_bps: int = 50,
    ) -> Quote:
        ...

    @abstractmethod
    def execute_swap(self, quote: Quote, signer: Optional[Signer] = None) -> dict:
        """Prepare + (if live) sign + broadcast. Returns a receipt dict.

        In simulation mode returns ``{"simulated": True, ...}`` and never
        broadcasts.
        """
        ...
