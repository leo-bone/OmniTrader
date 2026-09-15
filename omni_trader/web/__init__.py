"""Zero-dependency web server for OmniTrader (REST API + static SPA).

Everything here is Python's standard library: `http.server` for transport,
PBKDF2 + HMAC for auth, SSE for streaming progress. Run it with::

    omnitrader web --port 8787

or::
    python -m omni_trader.web.server
"""
from typing import Any

__all__ = ["OmniTraderServer", "serve", "run_backtest", "run_evolution"]


def __getattr__(name: str) -> Any:
    """Import the server module lazily.

    Importing `.server` eagerly here makes `python -m omni_trader.web.server`
    load it twice — once as `omni_trader.web.server` while initialising the
    package, then again as `__main__` — which Python flags with a
    RuntimeWarning. Deferring the import keeps both entry points clean.
    """
    if name in __all__:
        from . import server as _server
        return getattr(_server, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
