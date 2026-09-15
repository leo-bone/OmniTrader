"""Command-line interface for OmniTrader.

    omnitrader backtest --strategy momentum       # single configuration
    omnitrader evolve  --population 32            # genetic search
    omnitrader web     --port 8787                # web console + REST API
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .data import DataFeed
from .risk import RiskConfig
from .backtest.engine import BacktestEngine
from .strategies import REGISTRY
from .evolution import EvolutionConfig, EvolutionEngine, FitnessConfig


def _print_result(res) -> None:
    print("\n" + "=" * 52)
    print(f"  {res.symbol}  [{res.timeframe}]  strategy backtest")
    print("=" * 52)
    print(f"  Initial capital : {res.initial_capital:,.2f}")
    print(f"  Final equity    : {res.final_equity:,.2f}")
    print(f"  Total return    : {res.total_return_pct:+.2f}%")
    print(f"  CAGR            : {res.cagr_pct:+.2f}%")
    print(f"  Sharpe (ann.)   : {res.sharpe:.2f}")
    print(f"  Sortino (ann.)  : {res.sortino:.2f}")
    print(f"  Max drawdown    : {res.max_drawdown_pct:.2f}%")
    print(f"  Win rate        : {res.win_rate_pct:.1f}%")
    print(f"  Profit factor   : {res.profit_factor}")
    print(f"  Trades          : {res.num_trades}")
    print(f"  Periods/yr      : {res.periods_per_year:,.0f}")
    if res.halted:
        print(f"  ⚠ HALTED        : {res.halt_reason}")
    print("=" * 52)


# ----------------------------------------------------------------------
# backtest
# ----------------------------------------------------------------------
def cmd_backtest(argv=None) -> int:
    p = argparse.ArgumentParser(prog="omnitrader backtest",
                                description="Run one strategy configuration")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--strategy", required=True, choices=list(REGISTRY.keys()))
    p.add_argument("--data", help="path to OHLCV .json (generated sample if omitted)")
    p.add_argument("--regimes", action="store_true",
                   help="use the multi-regime synthetic generator instead of a random walk")
    p.add_argument("--bars", type=int, default=2000, help="synthetic bar count")
    p.add_argument("--seed", type=int, default=42, help="synthetic generator seed")
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--fee", type=float, default=0.001)
    p.add_argument("--slippage", type=float, default=0.0005)
    p.add_argument("--risk-per-trade", type=float, default=0.01)
    p.add_argument("--max-position-pct", type=float, default=0.30)
    p.add_argument("--daily-loss-limit", type=float, default=0.05)
    p.add_argument("--max-drawdown-limit", type=float, default=0.20)
    p.add_argument("--params", help='json string of strategy params')
    p.add_argument("--export", help="optional path to export equity curve json")
    args = p.parse_args(argv)

    if args.data:
        feed = DataFeed.from_json(args.data, symbol=args.symbol)
    elif args.regimes:
        feed = DataFeed.generate_regimes(symbol=args.symbol, n=args.bars, seed=args.seed)
        print(f"(using generated {len(feed)} bar multi-regime series "
              f"— pass --data for real history)")
    else:
        feed = DataFeed.generate_sample(symbol=args.symbol, n=args.bars, seed=args.seed)
        print(f"(using generated {len(feed)} bar sample — pass --data for real history)")

    strat_cls = REGISTRY[args.strategy]
    params = json.loads(args.params) if args.params else {}
    strategy = strat_cls(params)

    cfg = RiskConfig(
        risk_per_trade=args.risk_per_trade,
        max_position_pct=args.max_position_pct,
        daily_loss_limit=args.daily_loss_limit,
        max_drawdown_limit=args.max_drawdown_limit,
    )
    engine = BacktestEngine(feed, strategy, cfg, initial_capital=args.capital,
                            fee_rate=args.fee, slippage=args.slippage)
    res = engine.run()
    _print_result(res)

    if args.export:
        Path(args.export).write_text(json.dumps({
            "symbol": res.symbol, "equity_curve": res.equity_curve,
            "metrics": {k: v for k, v in res.to_dict().items()
                        if k not in ("equity_curve", "trades")},
        }, indent=2))
        print(f"equity curve exported -> {args.export}")
    return 0


# ----------------------------------------------------------------------
# evolve
# ----------------------------------------------------------------------
def cmd_evolve(argv=None) -> int:
    p = argparse.ArgumentParser(prog="omnitrader evolve",
                                description="Genetic search over strategy + risk genes")
    p.add_argument("--data", help="OHLCV .json (synthetic if omitted)")
    p.add_argument("--regimes", action="store_true",
                   help="use the multi-regime synthetic generator (default)")
    p.add_argument("--bars", type=int, default=3000)
    p.add_argument("--seed", type=int, default=42, help="evolution seed")
    p.add_argument("--symbol", default="SYNTHUSDT")
    p.add_argument("--strategies", default="momentum,mean_reversion,grid",
                   help="comma-separated species taking part in the competition")
    p.add_argument("--population", type=int, default=32)
    p.add_argument("--generations", type=int, default=12)
    p.add_argument("--elite", type=int, default=2)
    p.add_argument("--tournament", type=int, default=3)
    p.add_argument("--mutation-rate", type=float, default=0.35)
    p.add_argument("--mutation-scale", type=float, default=0.20)
    p.add_argument("--patience", type=int, default=6)
    p.add_argument("--train-frac", type=float, default=0.50)
    p.add_argument("--val-frac", type=float, default=0.25)
    p.add_argument("--capital", type=float, default=10000.0)
    p.add_argument("--fee", type=float, default=0.001)
    p.add_argument("--slippage", type=float, default=0.0005)
    p.add_argument("--metric", default="sharpe",
                   choices=["sharpe", "sortino", "cagr_pct", "total_return_pct", "calmar"])
    p.add_argument("--export", help="write the full result (champion + history) to JSON")
    args = p.parse_args(argv)

    if args.data:
        feed = DataFeed.from_json(args.data, symbol=args.symbol)
    elif args.regimes:
        feed = DataFeed.generate_regimes(symbol=args.symbol, n=args.bars, seed=args.seed)
        print(f"(using generated {len(feed)} bar multi-regime series "
              f"— pass --data for real history)")
    else:
        feed = DataFeed.generate_regimes(symbol=args.symbol, n=args.bars, seed=args.seed)
        print(f"(using generated {len(feed)} bar multi-regime series; "
              f"--data for real history)")

    cfg = EvolutionConfig(
        strategies=tuple(s.strip() for s in args.strategies.split(",") if s.strip()),
        population_size=args.population, generations=args.generations,
        elite_count=args.elite, tournament_size=args.tournament,
        mutation_rate=args.mutation_rate, mutation_scale=args.mutation_scale,
        patience=args.patience, seed=args.seed,
        train_frac=args.train_frac, val_frac=args.val_frac,
        initial_capital=args.capital, fee_rate=args.fee, slippage=args.slippage,
        fitness=FitnessConfig(primary=args.metric),
    )

    lines = []

    def on_gen(rec):
        print(f"  gen {rec.generation:>3} | best {rec.best_fitness:+8.3f} | "
              f"mean {rec.mean_fitness:+8.3f} | "
              f"species {_fmt_species(rec.species)}")
        lines.append(rec.to_dict())

    print(f"\nEvolving {len(cfg.strategies)} species on {len(feed)} bars "
          f"(metric={args.metric}, seed={args.seed})")
    print(f"  split: train {cfg.train_frac:.0%} / validation {cfg.val_frac:.0%} / "
          f"test {1 - cfg.train_frac - cfg.val_frac:.0%} (sealed)\n")

    result = EvolutionEngine(feed, cfg, on_generation=on_gen).run()

    print("\n" + "=" * 64)
    print(f"  CHAMPION: {result.champion.pretty()}")
    print("=" * 64)
    for label, seg, note in (
        ("IS   train     ", result.champion_is, "what selection optimised"),
        ("OOS  validation", result.champion_oos, "what it had to survive too"),
        ("TEST sealed    ", result.champion_test, "never seen during evolution"),
    ):
        print(f"  {label} | ret {seg.get('total_return_pct', 0):+8.2f}% | "
              f"sharpe {seg.get('sharpe', 0):+7.2f} | dd {seg.get('max_drawdown_pct', 0):6.2f}% | "
              f"trades {seg.get('num_trades', 0):>4}  ({note})")
    print("=" * 64)
    if getattr(result, "degraded_selection", False):
        print("  !! DEGRADED: no genome passed the eligibility gates, so this")
        print("     champion is just the highest raw fitness. Do not trade it.")
        print("=" * 64)
    print(f"  generations {result.generations_run} | evaluations {result.total_evaluations} "
          f"| {result.elapsed_sec:.1f}s | {result.stop_reason}")

    if args.export:
        Path(args.export).write_text(json.dumps(result.to_dict(), indent=2, default=str))
        print(f"\nfull result exported -> {args.export}")
    return 0


def _fmt_species(species: dict) -> str:
    return " ".join(f"{k}:{v}" for k, v in sorted(species.items()))


# ----------------------------------------------------------------------
# web
# ----------------------------------------------------------------------
def cmd_web(argv=None) -> int:
    from .web.server import serve

    p = argparse.ArgumentParser(prog="omnitrader web",
                                description="Start the web console")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--home", help="data directory for users.json / session secret")
    p.add_argument("--open", action="store_true", help="open a browser")
    p.add_argument("--no-auth", action="store_true",
                   help="disable login (loopback only; for a quick local look)")
    p.add_argument("--create-user", nargs=2, metavar=("USER", "PASSWORD"),
                   help="create (or reset) an account then exit")
    args = p.parse_args(argv)

    if args.create_user:
        from .web.auth import UserStore
        user, pw = args.create_user
        store = UserStore()
        try:
            store.create_user(user, pw)
            print(f"created user '{user}'")
        except ValueError:
            store.set_password(user, pw)
            print(f"updated password for '{user}'")
        return 0

    serve(args.host, args.port, home=args.home, open_browser=args.open,
          auth_required=not args.no_auth)
    return 0


def main(argv=None) -> int:
    """Dispatch to a subcommand. Unknown first args fall back to `backtest`,
    so the previous `omnitrader --strategy momentum` form still works."""
    argv = list(sys.argv[1:]) if argv is None else list(argv)
    if argv and argv[0] in ("web", "evolve", "backtest"):
        cmd = argv.pop(0)
        return {"web": cmd_web, "evolve": cmd_evolve, "backtest": cmd_backtest}[cmd](argv)
    return cmd_backtest(argv)


if __name__ == "__main__":
    sys.exit(main())
