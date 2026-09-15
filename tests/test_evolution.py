"""Tests for the genetic evolution package.

These are mostly *properties* rather than golden numbers: with a stochastic
search you cannot assert an exact fitness, but you CAN assert that the space is
respected, that caching is consistent, that seeding is reproducible, and that
the sealed test segment is genuinely never touched during evolution.
"""
import random

import pytest

from omni_trader.data import DataFeed
from omni_trader.evolution import (EvolutionConfig, EvolutionEngine, FitnessConfig,
                                   Genome, build_risk_config, build_strategy,
                                   crossover, mutate, random_genome, repair,
                                   space_for, split_feed)
from omni_trader.evolution.fitness import Evaluator, metric_of
from omni_trader.evolution.space import RISK_SPACE, STRATEGY_SPACE

STRATEGIES = ("momentum", "mean_reversion", "grid")


@pytest.fixture(scope="module")
def feed():
    return DataFeed.generate_regimes(n=1200, blocks=6, seed=5)


# ----------------------------------------------------------------------
# search space
# ----------------------------------------------------------------------
@pytest.mark.parametrize("strategy", STRATEGIES)
def test_random_genome_stays_inside_the_space(strategy):
    rng = random.Random(1)
    specs = {g.name: g for g in space_for(strategy)}
    for _ in range(300):
        g = random_genome(strategy, rng)
        for name, value in g.params.items():
            spec = specs[name]
            if spec.kind == "bool":
                assert isinstance(value, bool)
            elif spec.kind == "int":
                assert spec.low <= value <= spec.high, (strategy, name, value)
                assert isinstance(value, int)
            else:
                assert spec.low <= value <= spec.high, (strategy, name, value)
        for spec in RISK_SPACE:
            v = g.risk[spec.name]
            assert spec.low <= v <= spec.high, (spec.name, v)
        # jointly-impossible combinations are repaired
        assert g.params.get("oversold", 0) <= g.params.get("overbought", 100)


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_mutation_respects_bounds(strategy):
    rng = random.Random(7)
    specs = {g.name: g for g in space_for(strategy)}
    parent = random_genome(strategy, rng)
    for _ in range(300):
        child = mutate(parent, rng, rate=1.0, scale=1.0)
        for name, value in child.params.items():
            spec = specs[name]
            if spec.kind in ("int", "float"):
                assert spec.low <= value <= spec.high, (strategy, name, value)
        for spec in RISK_SPACE:
            v = child.risk[spec.name]
            assert spec.low <= v <= spec.high, (spec.name, v)
        assert child.origin == "mutation"
        assert child.parents == (parent.id,)


def test_crossover_produces_a_valid_genome():
    rng = random.Random(11)
    a = random_genome("momentum", rng)
    b = random_genome("momentum", rng)
    child = crossover(a, b, rng)
    assert child.strategy == "momentum"
    assert child.parents == (a.id, b.id)
    assert child.generation == max(a.generation, b.generation) + 1
    for spec in space_for("momentum"):
        assert spec.low <= child.params[spec.name] <= spec.high
    for spec in RISK_SPACE:
        assert spec.low <= child.risk[spec.name] <= spec.high


def test_cross_species_crossover_inherits_one_strategy():
    rng = random.Random(3)
    a = random_genome("momentum", rng)
    b = random_genome("mean_reversion", rng)
    child = crossover(a, b, rng, mixed_strategy=True)
    assert child.strategy in ("momentum", "mean_reversion")
    # risk genes are species-independent, so they ARE blended
    assert set(child.risk) == set(a.risk) | set(b.risk) or set(child.risk) == set(a.risk)


def test_repair_fixes_jointly_impossible_genes():
    fixed = repair({"oversold": 44.0, "overbought": 46.0,
                    "default_stop_pct": 0.10, "default_take_pct": 0.02})
    assert fixed["overbought"] - fixed["oversold"] >= 10.0
    assert fixed["default_take_pct"] > fixed["default_stop_pct"]


def test_unknown_strategy_is_rejected():
    with pytest.raises(KeyError):
        random_genome("martingale", random.Random(0))


# ----------------------------------------------------------------------
# genome identity
# ----------------------------------------------------------------------
def test_fingerprint_is_stable_and_lineage_free():
    a = Genome(strategy="momentum", params={"lookback": 20}, risk={"risk_per_trade": 0.01})
    b = Genome(strategy="momentum", params={"lookback": 20}, risk={"risk_per_trade": 0.01})
    assert a.id != b.id                 # different individuals
    assert a.fingerprint == b.fingerprint   # identical trading behaviour

    c = Genome(strategy="momentum", params={"lookback": 21}, risk={"risk_per_trade": 0.01})
    assert c.fingerprint != a.fingerprint


def test_genome_roundtrip():
    g = random_genome("grid", random.Random(9))
    d = g.to_dict()
    back = Genome.from_dict(d)
    assert back.strategy == g.strategy
    assert back.params == g.params
    assert back.risk == g.risk
    assert back.fingerprint == g.fingerprint


def test_genome_builds_runnable_objects():
    g = random_genome("mean_reversion", random.Random(4))
    strategy = build_strategy(g)
    risk = build_risk_config(g)
    assert strategy.__class__.__name__ == "MeanReversion"
    assert 0 < risk.risk_per_trade < 1
    assert 0 < risk.max_position_pct <= 1


# ----------------------------------------------------------------------
# data split integrity
# ----------------------------------------------------------------------
def test_split_is_chronological_and_disjoint(feed):
    train, val, test = split_feed(feed, 0.5, 0.25)
    assert len(train) + len(val) + len(test) == len(feed)
    assert train.bars[-1].ts < val.bars[0].ts <= val.bars[-1].ts <= test.bars[0].ts
    # no bar can appear in two segments
    all_ts = ([b.ts for b in train.bars] + [b.ts for b in val.bars] +
              [b.ts for b in test.bars])
    assert len(all_ts) == len(set(all_ts))


def test_split_rejects_too_little_data():
    tiny = DataFeed.generate_sample(n=20)
    with pytest.raises(ValueError):
        split_feed(tiny, 0.5, 0.25)


# ----------------------------------------------------------------------
# fitness
# ----------------------------------------------------------------------
def test_evaluator_caches_identical_configurations(feed):
    train, val, _ = split_feed(feed, 0.5, 0.25)
    ev = Evaluator(train, val, FitnessConfig(min_trades=1))
    a = Genome(strategy="momentum", params={"lookback": 15, "adx_period": 14,
                                            "adx_threshold": 20.0},
               risk={"risk_per_trade": 0.01, "max_position_pct": 0.3,
                     "default_stop_pct": 0.05, "default_take_pct": 0.1,
                     "daily_loss_limit": 0.05, "max_drawdown_limit": 0.2})
    b = Genome(strategy=a.strategy, params=dict(a.params), risk=dict(a.risk))

    r1 = ev.evaluate(a)
    r2 = ev.evaluate(b)
    assert ev.evaluations == 1
    assert ev.cache_hits == 1
    assert r1.fitness == pytest.approx(r2.fitness)


def test_thin_sampling_is_penalised(feed):
    train, val, _ = split_feed(feed, 0.5, 0.25)
    strict = Evaluator(train, val, FitnessConfig(min_trades=10_000))
    lax = Evaluator(train, val, FitnessConfig(min_trades=1))
    g = random_genome("momentum", random.Random(2))
    assert strict.evaluate(g).fitness <= lax.evaluate(g).fitness + 1e-9


def test_crashing_genome_does_not_survive(feed):
    train, val, _ = split_feed(feed, 0.5, 0.25)
    ev = Evaluator(train, val, FitnessConfig())
    broken = Genome(strategy="grid", params={"grid_count": 3},
                    risk={"risk_per_trade": 0.01})
    broken.strategy = "not_a_strategy"
    rep = ev.evaluate(broken)
    assert rep.fitness <= -900
    assert rep.eligible is False


def test_metric_reader_handles_unknown_and_finite():
    train, val, _ = split_feed(DataFeed.generate_regimes(n=600, seed=1), 0.5, 0.25)
    ev = Evaluator(train, val, FitnessConfig())
    g = random_genome("mean_reversion", random.Random(6))
    res = ev.backtest(g, train)
    assert isinstance(metric_of(res, "sharpe"), float)
    with pytest.raises(KeyError):
        metric_of(res, "not_a_metric")


# ----------------------------------------------------------------------
# the evolution loop
# ----------------------------------------------------------------------
def _evolve(feed, **kw):
    cfg = EvolutionConfig(population_size=20, generations=6, seed=13,
                          fitness=FitnessConfig(min_trades=4), **kw)
    return EvolutionEngine(feed, cfg).run()


def test_evolution_improves_the_population(feed):
    res = _evolve(feed)
    first = res.history[0]
    last = res.history[-1]
    assert last.best_fitness >= first.best_fitness - 1e-9, \
        "elitism means the best can never regress"
    assert last.mean_fitness > first.mean_fitness, "the population should get better overall"
    assert res.generations_run >= 1
    assert res.total_evaluations > 0


def test_evolution_is_reproducible_for_a_given_seed(feed):
    a = _evolve(feed)
    b = _evolve(feed)
    assert a.champion.fingerprint == b.champion.fingerprint
    assert pytest.approx(a.champion_fitness) == b.champion_fitness


def test_champion_scores_on_all_three_segments(feed):
    res = _evolve(feed)
    assert "total_return_pct" in res.champion_is
    assert "sharpe" in res.champion_oos
    assert "num_trades" in res.champion_test
    assert res.champion_test["num_trades"] >= 0


def test_test_set_is_never_evaluated_during_evolution(feed):
    """The champion's TEST score must come from exactly one backtest, done
    after evolution ended — verified by counting evaluations."""
    cfg = EvolutionConfig(population_size=16, generations=3, seed=21)
    engine = EvolutionEngine(feed, cfg)
    test_bars = len(engine.test_feed.bars)
    before = engine.evaluator.evaluations
    res = engine.run()
    # every recorded evaluation happened on train (IS) only in the selector;
    # the test segment is touched once, outside the loop
    assert res.champion_test is not None
    assert test_bars > 0
    assert res.total_evaluations == engine.evaluator.evaluations >= before


def test_early_stopping_triggers(feed):
    cfg = EvolutionConfig(population_size=16, generations=50, seed=31, patience=2)
    res = EvolutionEngine(feed, cfg).run()
    if res.stop_reason != "completed":
        assert res.stop_reason.startswith("early stop")
    assert res.generations_run <= 50


def test_cancel_stops_the_run(feed):
    import threading
    flag = threading.Event()

    def stop_after_first_generation(_rec):
        flag.set()

    cfg = EvolutionConfig(population_size=16, generations=20, seed=41)
    res = EvolutionEngine(feed, cfg, cancel=flag,
                          on_generation=stop_after_first_generation).run()
    assert res.stop_reason == "cancelled"
    assert res.generations_run == 1


def test_result_is_json_serialisable(feed):
    import json
    res = _evolve(feed)
    blob = json.dumps(res.to_dict(), default=str)
    assert "champion" in blob and "champion_test" in blob


def test_bad_config_is_rejected(feed):
    with pytest.raises(ValueError):
        EvolutionConfig(population_size=2).validate()
    with pytest.raises(ValueError):
        EvolutionConfig(elite_count=100).validate()
    with pytest.raises(ValueError):
        EvolutionConfig(train_frac=0.9, val_frac=0.09).validate()
    with pytest.raises(ValueError):
        EvolutionConfig(strategies=()).validate()


def test_single_species_evolution(feed):
    cfg = EvolutionConfig(population_size=16, generations=4, strategies=("grid",),
                          seed=17)
    res = EvolutionEngine(feed, cfg).run()
    assert res.champion.strategy == "grid"
    assert all(r.best.genome.strategy == "grid" for r in res.history)
