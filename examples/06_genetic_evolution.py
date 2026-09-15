#!/usr/bin/env python3
"""Genetic evolution over strategy + risk genes.

Run:  python3 examples/06_genetic_evolution.py

What it demonstrates:

  1. Why pure random-walk data is a terrible evolution playground — searching it
     only measures luck. Then the same search on multi-regime data, where there
     IS structure to find.
  2. The three-segment protocol: TRAIN drives selection, VALIDATION punishes
     overfitting, and TEST is sealed until the very end.
  3. Reading the champion's genome and turning it into a runnable strategy.
"""
from __future__ import annotations

from omni_trader.data import DataFeed
from omni_trader.evolution import (EvolutionConfig, EvolutionEngine, FitnessConfig,
                                   build_risk_config, build_strategy, describe_space)
from omni_trader.backtest.engine import BacktestEngine

REGIME_GENES = EvolutionConfig(
    strategies=("momentum", "mean_reversion", "grid"),
    population_size=32, generations=12, seed=7,
)


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


def main() -> None:
    # ------------------------------------------------------------------
    space = describe_space()
    total_genes = (sum(len(v) for v in space["strategies"].values())
                   + len(space["risk"]))
    section(f"1. THE SEARCH SPACE ({total_genes} evolvable genes)")
    for name, genes in space["strategies"].items():
        listing = ", ".join(
            f"{g['name']}[{g['low']}, {g['high']}]" if g["kind"] != "bool"
            else f"{g['name']}[bool]"
            for g in genes)
        print(f"  {name:15s} {listing}")
    print("  risk            " + ", ".join(
        f"{g['name']}[{g['low']}, {g['high']}]" for g in space["risk"]))
    print("\n  Risk genes live in the SAME genome as the strategy: a good entry")
    print("  signal with suicidal sizing is still a losing configuration.")

    def run(feed: DataFeed, cfg: EvolutionConfig, title: str) -> None:
        print(f"\n  --- {title} ---")
        engine = EvolutionEngine(feed, cfg)
        print(f"  split: train {len(engine.train_feed)} / "
              f"validation {len(engine.val_feed)} / test {len(engine.test_feed)} (sealed)")

        def on_gen(rec):
            species = " ".join(f"{k}:{v}" for k, v in sorted(rec.species.items()))
            print(f"    gen {rec.generation:>2} | best {rec.best_fitness:+7.3f} | "
                  f"mean {rec.mean_fitness:+7.3f} | {species}")

        result = EvolutionEngine(feed, cfg, on_generation=on_gen).run()
        print(f"\n  stop reason : {result.stop_reason}")
        print(f"  evaluations : {result.total_evaluations} in {result.elapsed_sec:.1f}s")
        print(f"  champion    : {result.champion.pretty()}")
        for label, seg in (("IS   ", result.champion_is),
                           ("OOS  ", result.champion_oos),
                           ("TEST ", result.champion_test)):
            print(f"    {label} ret {seg['total_return_pct']:+8.2f}% | "
                  f"sharpe {seg['sharpe']:+7.2f} | dd {seg['max_drawdown_pct']:6.2f}% | "
                  f"trades {seg['num_trades']:>3}")
        return result

    # ------------------------------------------------------------------
    section("2. CONTROL: searching a structureless random walk")
    walk = DataFeed.generate_sample(n=2500, seed=7)
    print("  A pure random walk has no exploitable edge. Whatever evolution")
    print("  'finds' here is curve-fitting, which is exactly why the TEST")
    print("  segment below exists.")
    run(walk, REGIME_GENES, "random walk (no structure)")

    # ------------------------------------------------------------------
    section("3. REAL SEARCH: multi-regime synthetic market")
    regimes = DataFeed.generate_regimes(n=2500, blocks=8, seed=7)
    result = run(regimes, REGIME_GENES, "multi-regime market")

    section("4. REUSING THE CHAMPION")
    champion = result.champion
    print(f"  strategy : {champion.strategy}")
    print(f"  params   : {champion.params}")
    print(f"  risk     : {champion.risk}")
    print(f"  lineage  : {' -> '.join(l['origin'] for l in result.lineage) or 'founder'}")

    strategy = build_strategy(champion)
    risk = build_risk_config(champion)
    replay = BacktestEngine(regimes, strategy, risk,
                            initial_capital=10_000.0).run()
    print(f"\n  Replayed on the FULL series (incl. the sealed test slice):")
    print(f"    return {replay.total_return_pct:+.2f}% | sharpe {replay.sharpe:+.2f} | "
          f"max dd {replay.max_drawdown_pct:.2f}% | trades {replay.num_trades}")

    print("\n  Same search, no random capital size — print it again with seed 7")
    print("  and you get the identical champion: evolution here is reproducible.")

    section("5. HOW TO CHANGE THE OBJECTIVE")
    print("  FitnessConfig controls what 'fittest' means. Examples:")
    for primary, cfg in (("sharpe", FitnessConfig()),
                         ("sortino", FitnessConfig(primary="sortino")),
                         ("cagr_pct", FitnessConfig(primary="cagr_pct")),
                         ("calmar", FitnessConfig(primary="calmar"))):
        print(f"    FitnessConfig(primary={primary!r})   # optimise {primary}")
    print("\n  And how hard overfitting is punished:")
    print("    FitnessConfig(oos_weight=0.8, gap_penalty=1.0, min_trades=30)")
    print("    -> heavier weight on unseen data, stronger decay penalty, and a")
    print("       demand for at least 30 trades before anything qualifies.")


if __name__ == "__main__":
    main()
