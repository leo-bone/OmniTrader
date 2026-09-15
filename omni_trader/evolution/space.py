"""Gene definitions and the search space each strategy is allowed to explore.

A *gene* here is one tunable knob (a strategy parameter or a risk parameter)
with a legal range. The space matters more than the optimiser: hand a genetic
algorithm an unbounded space and it will happily hand you a gorgeous,
perfectly-overfit monster. Every range below is deliberately bounded to values
that would survive contact with a real exchange.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence, Tuple

INT = "int"
FLOAT = "float"
BOOL = "bool"
CHOICE = "choice"


@dataclass(frozen=True)
class GeneSpec:
    """One evolvable knob.

    kind:
      int    -- integer in [low, high]
      float  -- real in [low, high]; `log=True` samples/mutates log-uniformly
                (right call for ranges spanning orders of magnitude)
      bool   -- True/False
      choice -- one of `values`
    """

    name: str
    kind: str = FLOAT
    low: float | None = None
    high: float | None = None
    values: Tuple[Any, ...] = ()
    log: bool = False
    help: str = ""

    # ------------------------------------------------------------------
    def sample(self, rng: random.Random) -> Any:
        if self.kind == BOOL:
            return rng.random() < 0.5
        if self.kind == CHOICE:
            return rng.choice(self.values)
        lo, hi = float(self.low), float(self.high)
        if self.log:
            v = math.exp(rng.uniform(math.log(max(lo, 1e-12)), math.log(hi)))
        else:
            v = rng.uniform(lo, hi)
        return self._cast(v)

    def mutate(self, value: Any, rng: random.Random, scale: float = 0.2,
               reset_prob: float = 0.08) -> Any:
        """Perturb `value` in place-ish.

        With probability `reset_prob` the gene is re-drawn from scratch (this
        is what keeps the population from getting stuck near the initial
        sample), otherwise it takes a gaussian step of `scale` x range.
        """
        if self.kind == BOOL:
            return not value if rng.random() < max(scale, 0.15) else bool(value)
        if self.kind == CHOICE:
            if len(self.values) <= 1:
                return value
            return rng.choice(self.values)
        lo, hi = float(self.low), float(self.high)
        if rng.random() < reset_prob:
            return self.sample(rng)
        if self.log:
            lv = math.log(max(float(value), 1e-12))
            step = scale * (math.log(hi) - math.log(max(lo, 1e-12)))
            v = math.exp(lv + rng.gauss(0, 1) * step)
        else:
            v = float(value) + rng.gauss(0, 1) * scale * (hi - lo)
        return self.clamp(v)

    def clamp(self, value: Any) -> Any:
        if self.kind == BOOL:
            return bool(value)
        if self.kind == CHOICE:
            return value if value in self.values else (self.values[0] if self.values else value)
        lo, hi = float(self.low), float(self.high)
        return self._cast(min(max(float(value), lo), hi))

    def _cast(self, v: float) -> float | int:
        return int(round(v)) if self.kind == INT else float(v)

    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "name": self.name, "kind": self.kind, "low": self.low,
            "high": self.high, "values": list(self.values), "log": self.log,
            "help": self.help,
        }


def G(name: str, low: float, high: float, kind: str = INT, **kw) -> GeneSpec:
    """Shorthand constructor (keeps the space tables readable)."""
    return GeneSpec(name=name, kind=kind, low=low, high=high, **kw)


# ----------------------------------------------------------------------
# Strategy parameter spaces
# ----------------------------------------------------------------------
STRATEGY_SPACE: Dict[str, Tuple[GeneSpec, ...]] = {
    "momentum": (
        G("lookback", 5, 120, INT, help="Donchian channel length"),
        G("adx_period", 5, 40, INT, help="ADX smoothing period"),
        G("adx_threshold", 10.0, 40.0, FLOAT, help="min ADX to call it a trend"),
    ),
    "mean_reversion": (
        G("rsi_period", 5, 40, INT, help="Wilder RSI length"),
        G("oversold", 10.0, 45.0, FLOAT, help="RSI below -> long"),
        G("overbought", 55.0, 90.0, FLOAT, help="RSI above -> short"),
        G("bb_period", 10, 60, INT, help="Bollinger window"),
        G("bb_mult", 1.0, 3.5, FLOAT, help="Bollinger sigma multiplier"),
        GeneSpec("bb_confirm", BOOL, help="require price outside the band"),
    ),
    "grid": (
        G("grid_count", 3, 40, INT, help="number of ladder lines"),
        G("grid_gap_pct", 0.002, 0.05, FLOAT, log=True, help="spacing between lines"),
        G("anchor_bars", 10, 400, INT, help="EMA period the ladder is centred on"),
    ),
}

# ----------------------------------------------------------------------
# Risk space — evolved together with the strategy. Sizing and stop placement
# are at least as important as the entry signal, and co-evolving them is the
# whole point of putting them in the same genome.
# ----------------------------------------------------------------------
RISK_SPACE: Tuple[GeneSpec, ...] = (
    G("risk_per_trade", 0.002, 0.03, FLOAT, log=True, help="fraction of equity risked"),
    G("max_position_pct", 0.05, 0.60, FLOAT, help="hard cap per position"),
    G("default_stop_pct", 0.01, 0.15, FLOAT, log=True, help="protective stop distance"),
    G("default_take_pct", 0.02, 0.40, FLOAT, log=True, help="take-profit distance"),
    G("daily_loss_limit", 0.02, 0.15, FLOAT, help="halt after this much daily loss"),
    G("max_drawdown_limit", 0.10, 0.50, FLOAT, help="halt at this drawdown"),
)


def repair(params: Dict[str, Any]) -> Dict[str, Any]:
    """Fix genes that are individually legal but jointly nonsense.

    Example: `oversold=44, overbought=56` is inside every range yet leaves no
    meaningful signal band. Constraints like this are why this function exists
    instead of relying purely on bounded ranges.
    """
    p = dict(params)
    # RSI: keep a real gap between the two thresholds
    if "oversold" in p and "overbought" in p:
        lo = float(p["oversold"])
        hi = float(p["overbought"])
        if hi - lo < 10.0:
            mid = (lo + hi) / 2.0
            p["oversold"] = max(10.0, mid - 5.0)
            p["overbought"] = min(90.0, mid + 5.0)
    # Stop must be tighter than the target, or nothing ever reaches take-profit
    if "default_stop_pct" in p and "default_take_pct" in p:
        stop = float(p["default_stop_pct"])
        take = float(p["default_take_pct"])
        if take <= stop:
            p["default_take_pct"] = min(0.40, stop * 2.0)
    return p


def space_for(strategy: str) -> Tuple[GeneSpec, ...]:
    if strategy not in STRATEGY_SPACE:
        raise KeyError(f"unknown strategy '{strategy}'; known: {sorted(STRATEGY_SPACE)}")
    return STRATEGY_SPACE[strategy]


def describe_space() -> Dict[str, Any]:
    """JSON-serialisable description used by the web UI to render controls."""
    return {
        "strategies": {
            name: [g.to_dict() for g in genes]
            for name, genes in STRATEGY_SPACE.items()
        },
        "risk": [g.to_dict() for g in RISK_SPACE],
    }


def sample_params(specs: Sequence[GeneSpec], rng: random.Random) -> Dict[str, Any]:
    return repair({g.name: g.sample(rng) for g in specs})
