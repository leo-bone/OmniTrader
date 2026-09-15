"""A genome = one complete, runnable trading configuration.

A genome owns *everything* needed to reproduce a backtest: which strategy
species it belongs to, that strategy's parameters, and the risk parameters
(size, stop, take, kill-switch levels) it trades under. Strategy + risk live in
the same genome on purpose — a good entry signal with suicidal sizing is still
a losing configuration, and co-evolving them is what makes "survival of the
fittest" mean anything here.
"""
from __future__ import annotations

import hashlib
import json
import random
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Sequence, Tuple

from ..strategies import REGISTRY as STRATEGY_REGISTRY
from .space import RISK_SPACE, repair, sample_params, space_for


def _short_id(*parts: Any) -> str:
    h = hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()
    return h[:10]


@dataclass
class Genome:
    strategy: str
    params: Dict[str, Any] = field(default_factory=dict)
    risk: Dict[str, Any] = field(default_factory=dict)
    id: str = ""
    generation: int = 0
    parents: Tuple[str, ...] = ()
    origin: str = "random"          # random | elite | crossover | mutation
    nickname: str = ""

    def __post_init__(self) -> None:
        self.params = repair(dict(self.params))
        self.risk = repair(dict(self.risk))
        if not self.id:
            self.id = _short_id(self.strategy, sorted(self.params.items()),
                                sorted(self.risk.items()), uuid.uuid4().hex[:6])

    # ------------------------------------------------------------------
    @property
    def fingerprint(self) -> str:
        """Identity of the CONFIGURATION (ignores id/lineage).

        Two genomes with the same fingerprint trade identically, so evaluation
        results can be cached — a big win once selection starts converging and
        the elite individuals get re-evaluated every generation.
        """
        blob = json.dumps(
            {"s": self.strategy,
             "p": {k: _round(v) for k, v in sorted(self.params.items())},
             "r": {k: _round(v) for k, v in sorted(self.risk.items())}},
            sort_keys=True, default=str,
        )
        return hashlib.sha1(blob.encode()).hexdigest()[:16]

    def short_id(self) -> str:
        return self.id[:8]

    def label(self) -> str:
        return self.nickname or f"{self.strategy}#{self.short_id()}"

    # ------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "strategy": self.strategy,
            "params": dict(self.params),
            "risk": dict(self.risk),
            "generation": self.generation,
            "parents": list(self.parents),
            "origin": self.origin,
            "nickname": self.nickname,
        }

    @classmethod
    def from_dict(cls, d: Mapping) -> "Genome":
        return cls(
            strategy=d["strategy"],
            params=dict(d.get("params", {})),
            risk=dict(d.get("risk", {})),
            id=d.get("id", ""),
            generation=int(d.get("generation", 0)),
            parents=tuple(d.get("parents", ())),
            origin=d.get("origin", "random"),
            nickname=d.get("nickname", ""),
        )

    def cli_args(self) -> Dict[str, Any]:
        """Everything needed to re-run this genome from the CLI."""
        return {"strategy": self.strategy, "params": dict(self.params),
                "risk": dict(self.risk)}

    def pretty(self) -> str:
        p = " ".join(f"{k}={v}" for k, v in sorted(self.params.items()))
        r = " ".join(f"{k}={v}" for k, v in sorted(self.risk.items()))
        return f"<{self.strategy}> {p} | {r}"


def _round(v: Any) -> Any:
    if isinstance(v, float):
        return round(v, 10)
    return v


# ----------------------------------------------------------------------
# Genetic operators
# ----------------------------------------------------------------------
def random_genome(strategy: str, rng: random.Random, generation: int = 0) -> Genome:
    """Draw a uniform-random individual of `strategy` across the legal space."""
    if strategy not in STRATEGY_REGISTRY:
        raise KeyError(f"unknown strategy '{strategy}'")
    params = sample_params(space_for(strategy), rng)
    risk = sample_params(RISK_SPACE, rng)
    return Genome(strategy=strategy, params=params, risk=risk,
                  generation=generation, origin="random")


def mutate(genome: Genome, rng: random.Random, rate: float = 0.35,
           scale: float = 0.2) -> Genome:
    """Copy a genome and perturb some of its genes.

    `rate` is the per-gene probability of being touched; `scale` controls how
    far a touched continuous gene can move, as a fraction of its range.
    """
    params, risk = dict(genome.params), dict(genome.risk)
    for spec in space_for(genome.strategy):
        if spec.name in params and rng.random() < rate:
            params[spec.name] = spec.mutate(params[spec.name], rng, scale=scale)
    for spec in RISK_SPACE:
        if spec.name in risk and rng.random() < rate:
            risk[spec.name] = spec.mutate(risk[spec.name], rng, scale=scale)
    return Genome(strategy=genome.strategy, params=params, risk=risk,
                  generation=genome.generation + 1, parents=(genome.id,),
                  origin="mutation")


def crossover(a: Genome, b: Genome, rng: random.Random,
              mixed_strategy: bool = False) -> Genome:
    """Blend two parents into one child.

    Same species -> per-gene uniform crossover on both strategy and risk genes
    (plus BLX-alpha style blending on continuous genes so children are not
    limited to values their parents happened to carry).

    Different species -> cross-species mating is mostly meaningless (what is a
    child of Donchian x RSI?), so we inherit one parent's strategy wholesale
    and only blend the risk genes, which ARE species-independent. Setting
    `mixed_strategy=True` additionally allows the child to switch species,
    letting selection compare strategies on equal footing.
    """
    if a.strategy == b.strategy:
        child_strategy = a.strategy
        params = _blend(a.params, b.params, space_for(a.strategy), rng)
    else:
        child_strategy = rng.choice([a.strategy, b.strategy]) if mixed_strategy else a.strategy
        parent_for_params = a if child_strategy == a.strategy else b
        other = b if parent_for_params is a else a
        params = _blend(parent_for_params.params, other.params,
                        space_for(child_strategy), rng, only_shared=True)
    risk = _blend(a.risk, b.risk, RISK_SPACE, rng)
    return Genome(
        strategy=child_strategy, params=params, risk=risk,
        generation=max(a.generation, b.generation) + 1,
        parents=(a.id, b.id), origin="crossover",
    )


def _blend(pa: Dict[str, Any], pb: Dict[str, Any], specs: Sequence, rng: random.Random,
           only_shared: bool = False) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    by_name = {s.name: s for s in specs}
    for k, va in pa.items():
        spec = by_name.get(k)
        vb = pb.get(k, None)
        if spec is None:
            out[k] = va
            continue
        if vb is None:
            out[k] = va if not only_shared else va
            continue
        if spec.kind == "bool":
            out[k] = va if rng.random() < 0.5 else vb
        elif spec.kind == "choice":
            out[k] = va if rng.random() < 0.5 else vb
        else:
            # BLX-alpha: sample in and slightly around the interval [va, vb]
            lo, hi = (va, vb) if va <= vb else (vb, va)
            alpha = 0.25
            span = hi - lo
            v = rng.uniform(lo - alpha * span, hi + alpha * span)
            out[k] = spec.clamp(v)
    # carry genes present only in b (keeps newcomers' unique traits alive)
    for k, vb in pb.items():
        if k not in out and not only_shared:
            out[k] = vb
    return repair(out)
