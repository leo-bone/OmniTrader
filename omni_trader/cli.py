"""Command-line interface for OmniTrader backtests."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .data import DataFeed
from .risk import RiskConfig
from .backtest.engine import BacktestEngine
from .strategies import REGISTRY


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


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="omnitrader", description="OmniTrader crypto backtester")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--strategy", required=True, choices=list(REGISTRY.keys()))
    p.add_argument("--data", help="path to OHLCV .json (generated sample if omitted)")
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
    else:
        feed = DataFeed.generate_sample(symbol=args.symbol)
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


if __name__ == "__main__":
    sys.exit(main())
