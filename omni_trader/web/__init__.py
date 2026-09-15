"""Zero-dependency web server for OmniTrader (REST API + static SPA).

Everything here is Python's standard library: `http.server` for transport,
PBKDF2 + HMAC for auth, SSE for streaming progress. Run it with::

    omnitrader web --port 8787

or::

    python -m omni_trader.web.server
"""
from .server import OmniTraderServer, run_backtest, run_evolution, serve

__all__ = ["OmniTraderServer", "serve", "run_backtest", "run_evolution"]
