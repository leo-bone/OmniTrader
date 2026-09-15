"""Fitness evaluation — where "fittest" gets its definition.

The scoring here is deliberately *not* "pick the params with the best backtest
return". That is a curve-fitting contest, and a genetic algorithm will win it
every single time while producing strategies that die on contact with data they
have never seen. The original `cryptogene` repo did exactly that and called it
evolution; this module is the replacement.

Every genome is scored on **two disjoint segments**:

  * IS  (in-sample / train) — what selection optimises
  * OOS (out-of-sample / validation) — what it must also survive

and the fitness explicitly penalises the gap between them. A third segment
(TEST) is never looked at during evolution at all; it is only scored once, at
the end, for the single champion that survived — that number is the honest
expectation of what you'd get live.

Fitness = weighted(IS, OOS) - gap_penalty * max(0, IS - OOS), minus additive
penalties for thin trading, excessive drawdown, and hitting a risk kill switch.
All penalties SUBTRACT: scaling a losing genome toward zero would make -5 into
-1.5, which reads as "better" to any max-fitness selector.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..backtest.engine import BacktestEngine, BacktestResult
from ..data import DataFeed
from ..risk import RiskConfig
from ..strategies import REGISTRY as STRATEGY_REGISTRY
from .genome import Genome


@dataclass
class FitnessConfig:
    """Knobs for how aggressively the evaluator punishes overfitting."""

    primary: str = "sharpe"          # sharpe | sortino | cagr_pct | total_return_pct | calmar
    is_weight: float = 0.35          # OOS is weighted higher on purpose
    oos_weight: float = 0.65
    gap_penalty: float = 0.60        # cost per unit of IS->OOS decay
    min_trades: int = 12             # below this a genome has not proven much
    thin_penalty: float = 3.0        # max fitness subtracted for a thin sample
    dd_cap_pct: float = 35.0         # drawdown allowed before penalty kicks in
    dd_penalty: float = 2.0          # subtracted per 100% of drawdown over the cap
    halt_penalty: float = 3.0        # subtracted when a risk kill-switch fires
    require_oos_trades: int = 4      # champion eligibility floor
    max_equity_points: int = 240     # downsampling for transport to the UI


_METRIC_FIELDS = {"sharpe", "sortino", "cagr_pct", "total_return_pct",
                  "max_drawdown_pct", "win_rate_pct", "profit_factor", "num_trades"}


def metric_of(res: BacktestResult, name: str) -> float:
    """Read one metric off a result, safely."""
    if name == "calmar":
        dd = res.max_drawdown_pct
        if dd <= 0:
            return 0.0
        return res.cagr_pct / dd
    v = getattr(res, name, None)
    if v is None:
        raise KeyError(f"unknown metric '{name}'")
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if not math.isfinite(f) else f


def _downsample(curve: List[float], n_max: int) -> List[float]:
    if len(curve) <= n_max:
        return [round(float(x), 4) for x in curve]
    step = len(curve) / float(n_max)
    out = []
    for k in range(n_max):
        lo = int(k * step)
        hi = max(lo + 1, int((k + 1) * step))
        seg = curve[lo:hi]
        out.append(round(sum(seg) / len(seg), 4))
    return out


def metrics_dict(res: BacktestResult) -> Dict[str, Any]:
    d = {}
    for k in ("total_return_pct", "cagr_pct", "sharpe", "sortino",
              "max_drawdown_pct", "win_rate_pct", "profit_factor",
              "num_trades", "final_equity", "halted"):
        v = getattr(res, k)
        if isinstance(v, float):
            v = 0.0 if not math.isfinite(v) else round(v, 4)
        d[k] = v
    d["calmar"] = round(metric_of(res, "calmar"), 4)
    if res.halt_reason:
        d["halt_reason"] = res.halt_reason
    return d


@dataclass
class FitnessReport:
    genome: Genome
    fitness: float
    is_metrics: Dict[str, Any] = field(default_factory=dict)
    oos_metrics: Dict[str, Any] = field(default_factory=dict)
    is_equity: List[float] = field(default_factory=list)
    oos_equity: List[float] = field(default_factory=list)
    eligible: bool = False
    notes: List[str] = field(default_factory=list)

    @property
    def generalization_gap(self) -> float:
        return float(self.is_metrics.get(self.primary_key, 0.0)) - \
               float(self.oos_metrics.get(self.primary_key, 0.0))

    primary_key: str = "sharpe"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "genome": self.genome.to_dict(),
            "fitness": round(self.fitness, 6),
            "is_metrics": self.is_metrics,
            "oos_metrics": self.oos_metrics,
            "is_equity": self.is_equity,
            "oos_equity": self.oos_equity,
            "eligible": self.eligible,
            "notes": self.notes,
            "gap": round(self.generalization_gap, 4),
        }


def build_strategy(genome: Genome):
    return STRATEGY_REGISTRY[genome.strategy](dict(genome.params))


def build_risk_config(genome: Genome) -> RiskConfig:
    valid = set(RiskConfig.__dataclass_fields__)
    kw = {k: v for k, v in genome.risk.items() if k in valid}
    return RiskConfig(**kw)


class Evaluator:
    """Runs one genome on the IS and OOS segments and scores the result."""

    def __init__(
        self,
        is_feed: DataFeed,
        oos_feed: DataFeed,
        config: Optional[FitnessConfig] = None,
        initial_capital: float = 10_000.0,
        fee_rate: float = 0.001,
        slippage: float = 0.0005,
        cache: Optional[Dict[str, FitnessReport]] = None,
    ):
        self.is_feed = is_feed
        self.oos_feed = oos_feed
        self.cfg = config or FitnessConfig()
        self.initial_capital = initial_capital
        self.fee_rate = fee_rate
        self.slippage = slippage
        self.cache: Dict[str, FitnessReport] = cache if cache is not None else {}
        self.evaluations = 0
        self.cache_hits = 0

    # ------------------------------------------------------------------
    def backtest(self, genome: Genome, feed: DataFeed) -> BacktestResult:
        engine = BacktestEngine(
            feed, build_strategy(genome), build_risk_config(genome),
            initial_capital=self.initial_capital,
            fee_rate=self.fee_rate, slippage=self.slippage,
        )
        return engine.run()

    def evaluate(self, genome: Genome) -> FitnessReport:
        """Score `genome`. Results are memoised by configuration fingerprint."""
        key = genome.fingerprint
        hit = self.cache.get(key)
        if hit is not None:
            self.cache_hits += 1
            # same trading config -> same trades; only lineage differs
            hit.genome = genome
            return hit
        self.evaluations += 1

        c = self.cfg
        notes: List[str] = []
        try:
            is_res = self.backtest(genome, self.is_feed)
            oos_res = self.backtest(genome, self.oos_feed)
        except Exception as exc:  # a genome that crashes simply does not survive
            notes.append(f"crash: {type(exc).__name__}: {exc}")
            rep = FitnessReport(genome=genome, fitness=-999.0, eligible=False,
                                notes=notes, primary_key=c.primary)
            self.cache[key] = rep
            return rep

        s_is = metric_of(is_res, c.primary)
        s_oos = metric_of(oos_res, c.primary)

        base = c.is_weight * s_is + c.oos_weight * s_oos
        gap = max(0.0, s_is - s_oos)
        fitness = base - c.gap_penalty * gap

        # NOTE: every penalty SUBTRACTS. Scaling a negative fitness toward zero
        # would silently reward bad genomes (-5 x 0.3 = -1.5, i.e. "better"),
        # which is a classic way to end up selecting losers.
        total_trades = is_res.num_trades + oos_res.num_trades
        if total_trades < c.min_trades:
            shortfall = (c.min_trades - total_trades) / float(c.min_trades)
            fitness -= c.thin_penalty * shortfall
            notes.append(f"thin sample: {total_trades} trades < {c.min_trades}")

        dd = max(is_res.max_drawdown_pct, oos_res.max_drawdown_pct)
        if dd > c.dd_cap_pct:
            fitness -= c.dd_penalty * (dd - c.dd_cap_pct) / 100.0
            notes.append(f"drawdown {dd:.1f}% > cap {c.dd_cap_pct:.0f}%")

        if is_res.halted or oos_res.halted:
            fitness -= c.halt_penalty
            notes.append("risk kill-switch fired")

        if not math.isfinite(fitness):
            fitness = -999.0
            notes.append("non-finite fitness")

        eligible = (oos_res.num_trades >= c.require_oos_trades
                    and total_trades >= c.min_trades // 2)

        rep = FitnessReport(
            genome=genome, fitness=fitness,
            is_metrics=metrics_dict(is_res), oos_metrics=metrics_dict(oos_res),
            is_equity=_downsample(is_res.equity_curve, c.max_equity_points),
            oos_equity=_downsample(oos_res.equity_curve, c.max_equity_points),
            eligible=eligible, notes=notes, primary_key=c.primary,
        )
        self.cache[key] = rep
        return rep

    def score_held_out(self, genome: Genome, feed: DataFeed) -> Dict[str, Any]:
        """Score the champion on data never used during evolution."""
        res = self.backtest(genome, feed)
        return metrics_dict(res)
