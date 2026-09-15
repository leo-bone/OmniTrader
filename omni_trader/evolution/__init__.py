"""Strategy evolution — genetic search over strategy + risk parameters.

This package exists because the original `cryptogene` repo performed evolution
*theatre*: it mutated parameters, reported the best backtest, and called it a
result — with no out-of-sample data, no cost model, and no penalty for
complexity. It would have handed you a beautiful loser.

What "real" means here:

    Genome     — strategy species + its parameters + risk parameters, together
    Population — a mixed-species pool competing for the same capital
    Fitness    — weighted TRAIN + VALIDATION score MINUS a penalty for the
                 gap between them (see `fitness.py`)
    Selection  — tournament selection + elitism
    TEST       — a third, sealed segment scored exactly once for the champion

Quick start:

    from omni_trader.data import DataFeed
    from omni_trader.evolution import EvolutionEngine, EvolutionConfig

    feed = DataFeed.from_json("data/BTCUSDT_1h.json")
    res = EvolutionEngine(feed, EvolutionConfig(population_size=32,
                                                generations=12)).run()
    print(res.champion.pretty(), res.champion_test)
"""
from .genome import Genome, crossover, mutate, random_genome
from .space import (GeneSpec, RISK_SPACE, STRATEGY_SPACE, describe_space,
                    repair, sample_params, space_for)
from .fitness import (Evaluator, FitnessConfig, FitnessReport, build_risk_config,
                      build_strategy, metric_of, metrics_dict)
from .engine import (EvolutionConfig, EvolutionEngine, EvolutionResult,
                     GenerationRecord, split_feed)

__all__ = [
    "Genome", "crossover", "mutate", "random_genome",
    "GeneSpec", "RISK_SPACE", "STRATEGY_SPACE", "describe_space",
    "repair", "sample_params", "space_for",
    "Evaluator", "FitnessConfig", "FitnessReport", "build_risk_config",
    "build_strategy", "metric_of", "metrics_dict",
    "EvolutionConfig", "EvolutionEngine", "EvolutionResult",
    "GenerationRecord", "split_feed",
]
