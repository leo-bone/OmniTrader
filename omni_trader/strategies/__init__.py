"""Strategy registry."""
from .base import Action, Context, Signal, Strategy
from .momentum import MomentumBreakout
from .mean_reversion import MeanReversion
from .grid import GridBot

REGISTRY = {
    "momentum": MomentumBreakout,
    "mean_reversion": MeanReversion,
    "grid": GridBot,
}

__all__ = [
    "Action", "Context", "Signal", "Strategy", "MomentumBreakout",
    "MeanReversion", "GridBot", "REGISTRY",
]
