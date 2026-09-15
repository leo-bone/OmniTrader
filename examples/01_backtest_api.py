"""示例 1：用 Python API 跑回测 + 参数扫描。

用法：
    python examples/01_backtest_api.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from omni_trader.backtest.engine import BacktestEngine  # noqa: E402
from omni_trader.data import DataFeed  # noqa: E402
from omni_trader.risk import RiskConfig  # noqa: E402
from omni_trader.strategies import REGISTRY  # noqa: E402


def run_one(name: str, feed: DataFeed, params: dict, capital: float = 10_000) -> dict:
    strat = REGISTRY[name](params)
    cfg = RiskConfig(
        risk_per_trade=0.01,      # 单笔最大风险 1% 权益
        max_position_pct=0.30,    # 单标的仓位上限 30%
        daily_loss_limit=0.05,    # 单日亏损 5% 熔断
        max_drawdown_limit=0.20,  # 总回撤 20% 熔断
    )
    eng = BacktestEngine(feed, strat, cfg, initial_capital=capital,
                         fee_rate=0.001, slippage=0.0005)
    r = eng.run()
    return {
        "params": params,
        "return_%": round(r.total_return_pct, 2),
        "sharpe": round(r.sharpe, 2),
        "maxdd_%": round(r.max_drawdown_pct, 2),
        "winrate_%": round(r.win_rate_pct, 1),
        "trades": r.num_trades,
        "halted": r.halted,
    }


def main() -> None:
    # 用内置样例数据；换成真实数据：DataFeed.from_json("your_ohlcv.json")
    feed = DataFeed.from_json(Path(__file__).resolve().parents[1] / "samples/BTCUSDT_1h.json")
    print(f"loaded {len(feed)} bars: {feed.symbol} [{feed.timeframe}]\n")

    print("=== 三个内置策略基准 ===")
    for name in ("momentum", "mean_reversion", "grid"):
        res = run_one(name, feed, {})
        print(f"  {name:15s} return={res['return_%']:+7.2f}%  sharpe={res['sharpe']:6.2f}  "
              f"maxdd={res['maxdd_%']:5.2f}%  trades={res['trades']:3d}  halted={res['halted']}")

    print("\n=== momentum 参数扫描（Donchian 通道长度 = lookback）===")
    for lb in (10, 20, 30, 55):
        res = run_one("momentum", feed, {"lookback": lb})
        print(f"  lookback={lb:<3d} return={res['return_%']:+7.2f}%  sharpe={res['sharpe']:6.2f}  "
              f"maxdd={res['maxdd_%']:5.2f}%  trades={res['trades']:3d}")

    print("\n=== grid 参数扫描（网格间距）===")
    for gap in (0.005, 0.01, 0.02):
        res = run_one("grid", feed, {"grid_gap_pct": gap, "grid_count": 10})
        print(f"  gap={gap:<6} return={res['return_%']:+7.2f}%  sharpe={res['sharpe']:6.2f}  "
              f"maxdd={res['maxdd_%']:5.2f}%  trades={res['trades']:3d}  halted={res['halted']}")

    print("\n=== 风控接线验证：把回撤上限压到 5% ===")
    strat = REGISTRY["grid"]({})
    tight = RiskConfig(max_drawdown_limit=0.05)
    r = BacktestEngine(feed, strat, tight, initial_capital=10_000).run()
    print(f"  halted={r.halted}  reason={r.halt_reason}")


if __name__ == "__main__":
    main()
