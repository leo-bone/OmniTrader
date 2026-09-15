"""The evolution loop — population, selection, reproduction, survival.

Design notes (this is the part the original `cryptogene` got wrong):

* **Three disjoint segments.** Fitness uses TRAIN + VALIDATION. TEST is sealed
  and scored exactly once, for the final champion. If you look at test data
  during evolution you have invented a very expensive random number generator.
* **Anti-clone.** A child whose configuration duplicates a living individual is
  re-mutated instead of added. Without this the population collapses into ten
  copies of one individual within a few generations and exploration stops.
* **Early stopping.** If the elite has not improved for `patience` generations
  we stop rather than grinding out generations that only add noise.
* **Determinism.** Everything is driven by one seeded `random.Random`, so the
  same seed reproduces the same run — essential for trusting a result you
  cannot yet explain.
"""
from __future__ import annotations

import random
import statistics
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..data import DataFeed
from .fitness import Evaluator, FitnessConfig, FitnessReport
from .genome import Genome, crossover, mutate, random_genome


@dataclass
class EvolutionConfig:
    strategies: Tuple[str, ...] = ("momentum", "mean_reversion", "grid")
    population_size: int = 40
    generations: int = 15
    elite_count: int = 2
    tournament_size: int = 3
    crossover_rate: float = 0.80
    mutation_rate: float = 0.35
    mutation_scale: float = 0.20
    patience: int = 6
    seed: int = 42
    mixed_strategy_crossover: bool = True
    # data split: train / validation are used for selection; test is sealed
    train_frac: float = 0.50
    val_frac: float = 0.25
    # trading costs
    initial_capital: float = 10_000.0
    fee_rate: float = 0.001
    slippage: float = 0.0005
    fitness: FitnessConfig = field(default_factory=FitnessConfig)

    def validate(self) -> None:
        if self.population_size < 4:
            raise ValueError("population_size must be >= 4")
        if self.generations < 1:
            raise ValueError("generations must be >= 1")
        if self.elite_count < 1 or self.elite_count >= self.population_size:
            raise ValueError("elite_count must be in [1, population_size)")
        if self.tournament_size < 2:
            raise ValueError("tournament_size must be >= 2")
        total = self.train_frac + self.val_frac
        if not (0.2 <= total <= 0.95):
            raise ValueError("train_frac + val_frac must leave data for the test segment")
        if not self.strategies:
            raise ValueError("at least one strategy is required")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategies": list(self.strategies),
            "population_size": self.population_size,
            "generations": self.generations,
            "elite_count": self.elite_count,
            "tournament_size": self.tournament_size,
            "crossover_rate": self.crossover_rate,
            "mutation_rate": self.mutation_rate,
            "mutation_scale": self.mutation_scale,
            "patience": self.patience,
            "seed": self.seed,
            "mixed_strategy_crossover": self.mixed_strategy_crossover,
            "train_frac": self.train_frac,
            "val_frac": self.val_frac,
            "initial_capital": self.initial_capital,
            "fee_rate": self.fee_rate,
            "slippage": self.slippage,
        }


@dataclass
class GenerationRecord:
    generation: int
    best_fitness: float
    mean_fitness: float
    worst_fitness: float
    std_fitness: float
    best: FitnessReport
    species: Dict[str, int] = field(default_factory=dict)
    evaluated: int = 0
    new_this_gen: int = 0
    elapsed_sec: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generation": self.generation,
            "best_fitness": round(self.best_fitness, 6),
            "mean_fitness": round(self.mean_fitness, 6),
            "worst_fitness": round(self.worst_fitness, 6),
            "std_fitness": round(self.std_fitness, 6),
            "species": dict(self.species),
            "evaluated": self.evaluated,
            "new_this_gen": self.new_this_gen,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "best_genome": self.best.genome.to_dict(),
            "best_is_metrics": self.best.is_metrics,
            "best_oos_metrics": self.best.oos_metrics,
            "best_label": self.best.genome.label(),
        }


@dataclass
class EvolutionResult:
    champion: Genome
    champion_fitness: float
    champion_is: Dict[str, Any]
    champion_oos: Dict[str, Any]
    champion_test: Dict[str, Any]
    runner_ups: List[FitnessReport]
    hall_of_fame: List[FitnessReport]
    history: List[GenerationRecord]
    config: Dict[str, Any]
    total_evaluations: int
    generations_run: int
    elapsed_sec: float
    stop_reason: str
    degraded_selection: bool = False  # True -> champion failed the eligibility gates
    lineage: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "champion": self.champion.to_dict(),
            "champion_fitness": round(self.champion_fitness, 6),
            "champion_is": self.champion_is,
            "champion_oos": self.champion_oos,
            "champion_test": self.champion_test,
            "hall_of_fame": [r.to_dict() for r in self.hall_of_fame],
            "history": [g.to_dict() for g in self.history],
            "config": self.config,
            "total_evaluations": self.total_evaluations,
            "generations_run": self.generations_run,
            "elapsed_sec": round(self.elapsed_sec, 3),
            "stop_reason": self.stop_reason,
            "degraded_selection": self.degraded_selection,
            "lineage": self.lineage,
        }


def split_feed(feed: DataFeed, train_frac: float,
               val_frac: float) -> Tuple[DataFeed, DataFeed, DataFeed]:
    """Split chronologically into train / validation / test. Never shuffled —
    shuffling time series is how people accidentally invent lookahead."""
    bars = feed.bars
    n = len(bars)
    if n < 60:
        raise ValueError(f"need >= 60 bars to split three ways, got {n}")
    i_train = max(30, int(n * train_frac))
    i_val = max(i_train + 30, int(n * (train_frac + val_frac)))
    i_val = min(i_val, n - 20)
    mk = lambda sl: DataFeed(feed.symbol, feed.timeframe, list(bars[sl]))  # noqa: E731
    return mk(slice(0, i_train)), mk(slice(i_train, i_val)), mk(slice(i_val, n))


class EvolutionEngine:
    """Runs a genetic search over strategy + risk genes."""

    def __init__(
        self,
        feed: DataFeed,
        config: Optional[EvolutionConfig] = None,
        on_generation: Optional[Callable[[GenerationRecord], None]] = None,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        cancel: Optional[threading.Event] = None,
        cache: Optional[Dict[str, FitnessReport]] = None,
    ):
        self.full_feed = feed
        self.cfg = config or EvolutionConfig()
        self.cfg.validate()
        self.rng = random.Random(self.cfg.seed)
        self.on_generation = on_generation
        self.on_progress = on_progress
        self.cancel = cancel or threading.Event()
        self.train_feed, self.val_feed, self.test_feed = split_feed(
            feed, self.cfg.train_frac, self.cfg.val_frac)
        self.evaluator = Evaluator(
            self.train_feed, self.val_feed, self.cfg.fitness,
            initial_capital=self.cfg.initial_capital,
            fee_rate=self.cfg.fee_rate, slippage=self.cfg.slippage,
            cache=cache,
        )
        self.population: List[Genome] = []
        self.reports: Dict[str, FitnessReport] = {}
        self.history: List[GenerationRecord] = []
        self.hall_of_fame: List[FitnessReport] = []
        self.lineage: Dict[str, Genome] = {}
        self._t0 = 0.0

    # ------------------------------------------------------------------
    # population bootstrap
    # ------------------------------------------------------------------
    def _seed_population(self) -> List[Genome]:
        pop: List[Genome] = []
        strategies = list(self.cfg.strategies)
        n = self.cfg.population_size
        for i in range(n):
            strat = strategies[i % len(strategies)]
            pop.append(random_genome(strat, self.rng, generation=0))
        return pop

    # ------------------------------------------------------------------
    def _evaluate_population(self, pop: List[Genome]) -> List[FitnessReport]:
        reports: List[FitnessReport] = []
        total = len(pop)
        for k, g in enumerate(pop):
            if self.cancel.is_set():
                break
            rep = self.evaluator.evaluate(g)
            self.reports[g.id] = rep
            self.lineage[g.id] = g
            reports.append(rep)
            if self.on_progress:
                self.on_progress(k + 1, total, f"genome {k+1}/{total}")
        reports.sort(key=lambda r: r.fitness, reverse=True)
        return reports

    def _tournament(self, scored: List[FitnessReport]) -> FitnessReport:
        k = min(self.cfg.tournament_size, len(scored))
        contenders = [scored[self.rng.randrange(0, len(scored))] for _ in range(k)]
        return max(contenders, key=lambda r: r.fitness)

    # ------------------------------------------------------------------
    def _reproduce(self, scored: List[FitnessReport]) -> List[Genome]:
        cfg = self.cfg
        children: List[Genome] = []
        live = [r.genome.fingerprint for r in scored]

        # elites survive untouched (this is what guarantees monotonic best)
        elites = [r.genome for r in scored[:cfg.elite_count]]
        for e in elites:
            child = Genome(strategy=e.strategy, params=dict(e.params),
                           risk=dict(e.risk), generation=e.generation + 1,
                           parents=(e.id,), origin="elite")
            children.append(child)

        seen = {g.fingerprint for g in children}
        target = cfg.population_size
        attempts = 0
        max_attempts = target * 12
        while len(children) < target and attempts < max_attempts:
            attempts += 1
            pa = self._tournament(scored).genome
            pb = self._tournament(scored).genome
            if pa is pb or self.rng.random() >= cfg.crossover_rate:
                child = mutate(pa, self.rng, rate=cfg.mutation_rate,
                               scale=cfg.mutation_scale)
            else:
                child = crossover(pa, pb, self.rng,
                                  mixed_strategy=cfg.mixed_strategy_crossover)
                if self.rng.random() < cfg.mutation_rate:
                    child = mutate(child, self.rng, rate=cfg.mutation_rate,
                                   scale=cfg.mutation_scale)
            if child.fingerprint in seen:
                # clone -> push it somewhere else instead of wasting a slot
                child = mutate(child, self.rng, rate=max(0.5, cfg.mutation_rate),
                               scale=cfg.mutation_scale)
            seen.add(child.fingerprint)
            children.append(child)
        return children[:target]

    # ------------------------------------------------------------------
    def run(self) -> EvolutionResult:
        self._t0 = time.time()
        cfg = self.cfg
        self.population = self._seed_population()
        best_ever: Optional[FitnessReport] = None
        best_eligible: Optional[FitnessReport] = None
        no_improve = 0
        stop_reason = "completed"
        gen = 0

        for gen in range(cfg.generations):
            if self.cancel.is_set():
                stop_reason = "cancelled"
                break
            scored = self._evaluate_population(self.population)
            if not scored:
                stop_reason = "cancelled"
                break

            fits = [r.fitness for r in scored]
            best = scored[0]
            species: Dict[str, int] = {}
            for r in scored:
                species[r.genome.strategy] = species.get(r.genome.strategy, 0) + 1

            self.hall_of_fame.extend(scored[:3])
            self.hall_of_fame.sort(key=lambda r: r.fitness, reverse=True)
            self.hall_of_fame = self.hall_of_fame[:25]

            rec = GenerationRecord(
                generation=gen,
                best_fitness=max(fits), mean_fitness=statistics.mean(fits),
                worst_fitness=min(fits),
                std_fitness=statistics.pstdev(fits) if len(fits) > 1 else 0.0,
                best=best, species=species,
                evaluated=self.evaluator.evaluations,
                new_this_gen=sum(1 for r in scored if r.genome.generation == gen),
                elapsed_sec=time.time() - self._t0,
            )
            self.history.append(rec)
            if self.on_generation:
                self.on_generation(rec)

            improved = best_ever is None or best.fitness > best_ever.fitness + 1e-9
            if improved:
                best_ever = best
                no_improve = 0
            else:
                no_improve += 1

            # Selection is driven by raw fitness, but only an *eligible* genome
            # may be crowned. Track the best one separately so a high-scoring
            # genome that lost money on its own training segment cannot win on
            # an accident of the validation split.
            if best.eligible:
                if best_eligible is None or best.fitness > best_eligible.fitness + 1e-9:
                    best_eligible = best

            if no_improve >= cfg.patience:
                stop_reason = f"early stop: no improvement for {cfg.patience} generations"
                break

            self.population = self._reproduce(scored)

        if best_ever is None:
            if self.cancel.is_set():
                raise RuntimeError("evolution cancelled before any evaluations completed")
            raise RuntimeError("evolution produced no evaluations")

        # Prefer an eligible champion. Falling back to the raw best is a last
        # resort; whether that happened travels in `degraded_selection` rather
        # than in stop_reason, which stays purely about why the loop ended.
        champ_report = best_eligible if best_eligible is not None else best_ever
        degraded = best_eligible is None

        champion = champ_report.genome
        test_metrics = self.evaluator.score_held_out(champion, self.test_feed)
        champ_is = champ_report.is_metrics
        champ_oos = champ_report.oos_metrics
        # reconstruct the champion's ancestry for display
        lineage: List[Dict[str, Any]] = []
        cur: Optional[Genome] = champion
        hops = 0
        while cur is not None and hops < 12:
            lineage.append({"id": cur.id, "strategy": cur.strategy,
                            "origin": cur.origin, "generation": cur.generation,
                            "parents": list(cur.parents)})
            hops += 1
            if not cur.parents:
                break
            cur = self.lineage.get(cur.parents[0])

        return EvolutionResult(
            champion=champion, champion_fitness=champ_report.fitness,
            champion_is=champ_is, champion_oos=champ_oos,
            champion_test=test_metrics, degraded_selection=degraded,
            runner_ups=self.hall_of_fame[1:6],
            hall_of_fame=self.hall_of_fame,
            history=list(self.history),
            config=cfg.to_dict(),
            total_evaluations=self.evaluator.evaluations,
            generations_run=len(self.history),
            elapsed_sec=time.time() - self._t0,
            stop_reason=stop_reason,
            lineage=lineage,
        )
