"""BSC on-chain execution via PancakeSwap V2 router.

Same safety model as :mod:`omni_trader.onchain.solana`:
  - ``simulation=True`` by default (quotes real via the on-chain router view,
    execution mocked).
  - Live signing only with a real :class:`EnvSigner` (``BSC_PRIVATE_KEY``).
  - ``web3`` is imported lazily.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from .base import OnChainExecutor, Quote, Signer

PANCAKE_ROUTER = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
BUSD = "0xe9e7CEA3DedcA5984780Bafc599bE2b3b2cA0EeB"
BSC_RPC = "https://bsc-dataseed.binance.org/"


class PancakeSwapClient(OnChainExecutor):
    chain = "bsc"

    def __init__(self, simulation: bool = True, rpc: str = BSC_RPC):
        self.simulation = simulation
        self.rpc = rpc

    def _web3(self):
        from web3 import Web3  # type: ignore

        return Web3(Web3.HTTPProvider(self.rpc))

    def get_quote(
        self,
        input_mint: str,
        output_mint: str,
        amount: Decimal,
        slippage_bps: int = 50,
    ) -> Quote:
        w3 = self._web3()
        router = w3.eth.contract(
            address=w3.to_checksum_address(PANCAKE_ROUTER),
            abi=[
                {
                    "name": "getAmountsOut",
                    "type": "function",
                    "stateMutability": "view",
                    "inputs": [
                        {"name": "amountIn", "type": "uint256"},
                        {"name": "path", "type": "address[]"},
                    ],
                    "outputs": [{"name": "amounts", "type": "uint256[]"}],
                }
            ],
        )
        decimals_in = 18
        if input_mint.lower() == WBNB.lower():
            decimals_in = 18
        base_in = int((amount * (Decimal(10) ** decimals_in)).to_integral_value())
        amounts = router.functions.getAmountsOut(base_in, [input_mint, output_mint]).call()
        out = Decimal(amounts[-1]) / (Decimal(10) ** 18)
        return Quote(
            input_mint=input_mint,
            output_mint=output_mint,
            in_amount=amount,
            out_amount=out,
            price_impact_pct=Decimal(0),  # real impact needs reserves; placeholder
            raw={"amountsOut": [str(a) for a in amounts]},
        )

    def execute_swap(self, quote: Quote, signer: Optional[Signer] = None, live: bool = False) -> dict:
        if not live or self.simulation:
            return {
                "simulated": True,
                "chain": self.chain,
                "in": float(quote.in_amount),
                "out_est": float(quote.out_amount),
                "note": "Set live=True with a Signer to broadcast a real swap.",
            }
        if signer is None:
            signer = EnvSigner("BSC_PRIVATE_KEY")
        # Real signing path requires web3 account + router ABI; delegated to
        # the injected signer so custody stays with the user.
        raise NotImplementedError(
            "Live BSC broadcast requires a configured web3 signer; wire your "
            "EnvSigner into a web3 account and call router.functions."
            "swapExactTokensForTokens via the signer."
        )
