"""OmniTrader — open-source adaptive crypto quant trading framework.

Consolidates the best ideas of the author's 4 repos
(QuantAgent / quant-trading / nexus-terminal / cryptogene) into one
production-grade, auditable, self-hostable package:

  * zero-lookahead event backtester (next-bar-open fills, fees, slippage)
  * correct equity accounting for long AND short positions
  * a risk manager whose kill-switches are ACTUALLY wired into the loop
    (daily-loss limit + max-drawdown halt), unlike the original repos
  * clean strategy interface + momentum / mean-reversion / grid strategies
  * Decimal-safe quantity rounding for live Binance execution
  * CLI, sample data, and pytest coverage

License: MIT
"""
from .data import Bar, DataFeed
from .risk import RiskManager, RiskConfig
from .backtest.engine import BacktestEngine, BacktestResult
from .strategies.base import Strategy, Signal, Action
from .strategies import MomentumBreakout, MeanReversion, GridBot
from .broker import SimulatedBroker, round_step_size

__version__ = "0.1.0"
__all__ = [
    "Bar", "DataFeed", "RiskManager", "RiskConfig", "BacktestEngine",
    "BacktestResult", "Strategy", "Signal", "Action", "MomentumBreakout",
    "MeanReversion", "GridBot", "SimulatedBroker", "round_step_size",
]
