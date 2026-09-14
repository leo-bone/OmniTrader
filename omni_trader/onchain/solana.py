"""Solana on-chain execution via the Jupiter aggregator.

Jupiter is an audited, non-custodial DEX aggregator. This client:
  - fetches quotes (public, no API key),
  - builds the swap transaction through Jupiter's ``/swap`` endpoint,
  - signs + sends ONLY in live mode with a real :class:`EnvSigner`.

Safety:
  - ``simulation=True`` by default -> quotes are real, execution is mocked.
  - No private key is ever read unless you call ``execute_swap(..., live=True)``.
  - Slippage is explicit (basis points).
"""
from __future__ import annotations

import json
import os
import urllib.request

from decimal import Decimal
from typing import Optional

from .base import OnChainExecutor, Quote, Signer

JUPITER_QUOTE = "https://quote-api.jup.ag/v6/quote"
JUPITER_SWAP = "https://quote-api.jup.ag/v6/swap"
# Well-known mints (base units = lamports for SOL, 6 dp for USDC)
SOL_MINT = "So11111111111111111111111111111111111111112"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
LAMPORTS = Decimal(1_000_000_000)


class JupiterClient(OnChainExecutor):
    chain = "solana"

    def __init__(self, simulation: bool = True, rpc: str = "https://api.mainnet-beta.solana.com"):
        self.simulation = simulation
        self.rpc = rpc

    # -- unit helpers ----------------------------------------------------
    @staticmethod
    def sol_to_lamports(amount_sol: Decimal) -> int:
        return int((amount_sol * LAMPORTS).to_integral_value())

    @staticmethod
    def ui_amount(raw: int, decimals: int = 9) -> Decimal:
        return Decimal(raw) / (Decimal(10) ** decimals)

    # -- quote -----------------------------------------------------------
    def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: Decimal,
        slippage_bps: int = 50,
    ) -> Quote:
        # amount is in *input token UI units*; convert to base units.
        decimals_in = 9 if input_mint == SOL_MINT else 6
        base_amount = int((amount * (Decimal(10) ** decimals_in)).to_integral_value())
        url = (
            f"{JUPITER_QUOTE}?inputMint={input_mint}&outputMint={output_mint}"
            f"&amount={base_amount}&slippageBps={slippage_bps}"
        )
        raw = self._get_json(url)
        in_amt = Decimal(raw.get("inAmount", 0)) / (Decimal(10) ** decimals_in)
        out_dec = 9 if output_mint == SOL_MINT else 6
        out_amt = Decimal(raw.get("outAmount", 0)) / (Decimal(10) ** out_dec)
        impact = Decimal(str(raw.get("priceImpactPct", "0")))
        return Quote(
            input_mint=input_mint,
            output_mint=output_mint,
            in_amount=in_amt,
            out_amount=out_amt,
            price_impact_pct=impact,
            raw=raw,
        )

    # -- execute ---------------------------------------------------------
    def execute_swap(self, quote: Quote, signer: Optional[Signer] = None, live: bool = False) -> dict:
        if not live or self.simulation:
            return {
                "simulated": True,
                "chain": self.chain,
                "in": float(quote.in_amount),
                "out_est": float(quote.out_amount),
                "price_impact_pct": float(quote.price_impact_pct),
                "note": "Set live=True with a Signer to broadcast a real swap.",
            }

        if signer is None:
            signer = EnvSigner("SOLANA_PRIVATE_KEY")
        pub = signer.public_key()

        body = {
            "quoteResponse": quote.raw,
            "userPublicKey": pub,
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnits": True,
            "prioritizationFeeLamports": "auto",
        }
        resp = self._post_json(JUPITER_SWAP, body)
        tx_b64: str = resp["swapTransaction"]

        # Decode (base64) -> sign (solders) -> base64 re-encode -> send.
        import base64

        from solders.transaction import VersionedTransaction  # type: ignore

        tx_bytes = base64.b64decode(tx_b64)
        vtx = VersionedTransaction.from_bytes(tx_bytes)
        signed = signer.sign_raw(bytes(vtx.message()))
        # Reconstruct a signed transaction for sending.
        signed_vtx = VersionedTransaction(vtx.message(), [signed])  # type: ignore
        encoded = base64.b64encode(bytes(signed_vtx)).decode()
        return self._send_tx(encoded)

    # -- rpc helpers -----------------------------------------------------
    def _send_tx(self, encoded: str) -> dict:
        payload = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    encoded,
                    {"encoding": "base64", "skipPreflight": False},
                ],
            }
        ).encode()
        req = urllib.request.Request(
            self.rpc,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    # -- http ------------------------------------------------------------
    @staticmethod
    def _get_json(url: str) -> dict:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())

    @staticmethod
    def _post_json(url: str, body: dict) -> dict:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode())
