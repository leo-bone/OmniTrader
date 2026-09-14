"""On-chain execution layer (Solana / BSC).

Design rules (security-first, same as the CEX layer):
  - Every executor is in *simulation* mode by default. Nothing touches real
    funds unless you explicitly pass ``simulation=False`` AND a signer with a
    real private key loaded from an environment variable.
  - Private keys are NEVER logged, printed, or committed. They are read only
    from env vars (``SOLANA_PRIVATE_KEY`` / ``BSC_PRIVATE_KEY``) via the
    :class:`EnvSigner`, and only when live mode is requested.
  - Heavy chain libraries (``web3``, ``solders``, ``base58``) are imported
    lazily so this package imports cleanly without them installed.

The framework does NOT implement custody. It prepares + routes swaps through
audited aggregators / routers (Jupiter for Solana, PancakeSwap V2 for BSC) and
delegates signing to an injected :class:`Signer` you control.
"""
from .base import OnChainExecutor, Signer, Quote
from .signer import EnvSigner
from .solana import JupiterClient
from .bsc import PancakeSwapClient

__all__ = [
    "OnChainExecutor",
    "Signer",
    "Quote",
    "EnvSigner",
    "JupiterClient",
    "PancakeSwapClient",
]
